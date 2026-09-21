"""
Dry-run-by-default repair tool for `Ingredient`/`Product` rows persisted
BEFORE the ingredient-language pipeline (see
`app.services.ingredient_catalog.materialize_ingredients`) ran on their
text. Before that fix, mixed-language (e.g. Romanian) OCR/Gemini
ingredient names could be persisted into the shared catalog's
`Ingredient.common_name` mislabeled as canonical English/Bulgarian, and
a product's `raw_ingredient_text` could likewise hold un-flagged
non-English/non-Bulgarian text. This module finds and (optionally)
repairs those OLD rows -- it does not change how NEW scans behave
(that is `ingredient_catalog`'s job, already fixed and out of scope
here).

Usage (mirrors `app.seed.load_seed`'s own invocation convention)::

    python -m app.seed.repair_ingredient_language           # dry run (default) -- report only, zero writes
    python -m app.seed.repair_ingredient_language --apply   # actually persist repairs

Detection (task requirement 6), per `Ingredient` row:

  1. `language_detection.detect_language(common_name)`. `"en"`/`"bg"`/
     `"unknown"` -> nothing to do (`ALREADY_FINE`), regardless of
     `source` -- an already-correct row is never touched even if its
     source happens to be OCR_HEURISTIC/GEMINI.
  2. Otherwise (`"other"`, i.e. a genuine foreign-language candidate):
     a. `source` is `CURATED_SEED` or `REGULATORY_LOOKUP` (see
        `app.models.enums.TRUSTED_INGREDIENT_SOURCES`) -> trusted
        human/regulatory data is NEVER repaired here, even though it
        superficially looks non-English/non-Bulgarian
        (`SKIPPED_TRUSTED_SOURCE`).
     b. `ingredient_segmentation.detect_ambiguous_segmentation(common_name)`
        returns a reason code -> this token looks like a merged/
        mis-segmented OCR fragment, not one reliable ingredient name.
        Translation alone must never certify identity for a token like
        this (same rule `ingredient_catalog._resolve_ingredient_languages`
        already applies to new scans) -- reported as
        `FLAGGED_UNRESOLVED_AMBIGUOUS` with the reason code, NEVER
        auto-repaired. A human (or another label photo) has to resolve
        this, not this tool.
     c. Otherwise: a genuine translation candidate. Every such row from
        one run is batched into ONE
        `ingredient_translation.translate_ingredient_tokens` call
        (full-list context, same batching this codebase already uses
        for a live scan). A `reliable` result becomes `TRANSLATED`; an
        unreliable one (Gemini unconfigured/unavailable, low
        confidence, a failed invariant check -- see
        `ingredient_translation`'s own docstring) becomes
        `FLAGGED_UNRESOLVED_TRANSLATION_FAILED` -- this tool never
        guesses a translation.

`Product.raw_ingredient_text` is report-only (task requirement 8): a
barcode whose `raw_ingredient_text` doesn't look English/Bulgarian is
listed so a human can decide (e.g. ask the user to rescan) -- this tool
never rewrites `Product.raw_ingredient_text` or `Product.ingredient_ids`
itself, since changing a product's stored ingredient ids has knock-on
correctness implications for
`app.services.ocr_normalizer.reconstruct_synthetic_ingredient` that are
genuinely out of scope for a repair tool that must never lose/corrupt
identity.

Applying a repair (task requirement 7), for one `TRANSLATED` row:

  1. The row's CURRENT (pre-repair) `common_name` is registered as its
     own `IngredientAlias` FIRST (language-tagged with the detected
     source language, `source=GEMINI`) -- task requirement 4: the
     original text must survive somewhere before/as the canonical
     `common_name` is overwritten. Idempotent: if this exact alias
     already exists (e.g. it was already registered back when the row
     was first created), `IngredientAlias.alias_normalized` being
     globally unique means this is a no-op reuse of the existing row,
     never a duplicate/conflicting write.
  2. `common_name`/`normalized_name` are updated to the verified English
     translation (`normalized_name` recomputed via
     `ingredient_normalization.normalize_ingredient_name`, never
     hand-derived).
  3. `source` is promoted to `GEMINI` UNLESS it already outranks GEMINI
     in `ingredient_catalog.SOURCE_PRIORITY` (never true for a row that
     reached this branch, since only OCR_HEURISTIC/GEMINI rows are ever
     translated here -- CURATED_SEED/REGULATORY_LOOKUP rows are always
     `SKIPPED_TRUSTED_SOURCE` above -- but the rank check is applied
     rather than hardcoding "always GEMINI", so this stays correct even
     if that invariant ever changes).
  4. The NEW (translated) text is ALSO registered as its own alias of
     the SAME ingredient id (best-effort, same pattern as step 1) --
     without this, a future scan of the same (now-English) label text
     would find no alias for it and could create an entirely separate
     duplicate `Ingredient` row instead of converging back onto this
     one (see `app.services.ingredient_catalog.get_or_create_catalog_ingredient`,
     which resolves identity via the alias table, never by scanning
     `Ingredient.normalized_name` directly).

Every step above only ever repairs `common_name`/`normalized_name`/
`source` plus alias rows -- it never deletes a row, never touches
`Product.ingredient_ids`, and never fabricates a scientific/regulatory
field.

Transactionality and idempotency (task requirement 3): one call to
`run_repair(db, apply=True)` is ONE logical batch -- every row's
alias-then-update write is wrapped in its own SAVEPOINT
(`AsyncSession.begin_nested`, the same pattern already used throughout
`app/repositories/`) so a single row's unexpected failure can't corrupt
or half-apply another row's already-staged change, but the whole run
is committed ONCE, together, at the very end. If the process is
interrupted before that final commit, nothing from this run persists
(safe to just re-run). Idempotent by construction, not by a separate
"already repaired" check: a repaired row's `common_name` is now
genuinely English, so `detect_language` classifies it `"en"` on the
very next run and it falls out as `ALREADY_FINE` with zero further
writes; dry-run mode never begins/commits any write at all (it still
calls `translate_ingredient_tokens` -- a read-only Gemini call -- to
report what a real repair WOULD translate a candidate to, but that is
the only outbound call it makes; the DB session is only ever read from
and is explicitly rolled back, never committed, when `apply=False`).

Report shape (task requirement 9) -- `RepairReport.to_dict()`, the same
structure both dry-run and apply modes print (as indented JSON) when
run as `python -m app.seed.repair_ingredient_language`::

    {
      "mode": "dry_run" | "apply",
      "generatedAt": "<ISO-8601 UTC timestamp>",
      "ingredientCounts": {
        "alreadyFine": <int>,
        "translated": <int>,
        "flaggedUnresolvedAmbiguous": <int>,
        "flaggedUnresolvedTranslationFailed": <int>,
        "skippedTrustedSource": <int>
      },
      "ingredientDetails": [
        {
          "ingredientId": "<Ingredient.id>",
          "commonName": "<Ingredient.common_name BEFORE this run>",
          "normalizedName": "<Ingredient.normalized_name BEFORE this run>",
          "source": "<IngredientSource value BEFORE this run>",
          "detectedLanguage": "other" | "en" | "bg" | "unknown",
          "category": "ALREADY_FINE" | "TRANSLATED"
                       | "FLAGGED_UNRESOLVED_AMBIGUOUS"
                       | "FLAGGED_UNRESOLVED_TRANSLATION_FAILED"
                       | "SKIPPED_TRUSTED_SOURCE",
          "reason": "<ambiguous-segmentation reason code, or"
                     " 'TRANSLATION_UNRELIABLE', or null>",
          "proposedTranslation": "<verified English translation, only"
                                   " when category == TRANSLATED, else null>",
          "applied": <bool -- true only when category == TRANSLATED AND"
                      " apply=True actually wrote it>,
          "translationFailureReason": "<closed-vocabulary internal reason,"
                     " only when category == FLAGGED_UNRESOLVED_TRANSLATION_FAILED,"
                     " else null -- see translationFailureReasons below>",
          "translationProviderFailureCategory": "<closed-vocabulary provider"
                     " sub-category, only when the reason is providerUnavailable,"
                     " else null>"
        },
        ...
      ],
      "translationFailureReasons": {   # additive; ALWAYS every key, zeros included
        "providerUnavailable": <int>, "malformedResponse": <int>,
        "noMatchingEntry": <int>, "lowConfidence": <int>,
        "emptyTranslation": <int>, "languageRejected": <int>,
        "eNumberMismatch": <int>, "numericMismatch": <int>,
        "unspecified": <int>
      },
      "translationProviderFailureCategories": {   # additive; every key;
        "notConfigured": <int>, "timeout": <int>, "transport": <int>,  # sums to
        "httpAuth": <int>, "httpRateLimited": <int>,                    # providerUnavailable
        "httpClientError": <int>, "httpServerError": <int>,
        "httpOther": <int>, "unparsableResponse": <int>, "unknown": <int>
      },
      "translationAttempt": {   # additive; the ONE batched call this run makes
        "batches": <0 or 1>, "targets": <int>, "reliable": <int>,
        "rejected": <int>,
        "outcome": "not_attempted" | "succeeded" | "partial" | "failed"
      },
      "productFlaggedCount": <int>,
      "productFlags": [
        {
          "barcode": "<Product.barcode>",
          "detectedLanguage": "other" | "unknown",
          "rawIngredientTextPreview": "<Product.raw_ingredient_text, truncated to 200 chars>"
        },
        ...
      ]
    }

The `translationFailureReasons` values always sum to
`ingredientCounts.flaggedUnresolvedTranslationFailed` (every such entry
carries exactly one reason; `translationProviderFailureCategories` sums
to `providerUnavailable`). They are aggregate counters/enums only --
never ingredient text, responses, exception messages or secrets -- so a
dry run explains WHY entries were flagged (provider not configured vs
transport/HTTP failure vs malformed/omitted response vs low confidence
vs language/E-number/number rejection) without re-running a paid
translation. `TRANSLATION_UNRELIABLE` remains the (only) per-entry
`reason` string, unchanged.

Only `ALREADY_FINE` rows are omitted from `ingredientCounts` being
`0` when there is genuinely nothing else to report -- every row from
`ingredient_repository.get_all` is represented exactly once across
`ingredientDetails` (including `ALREADY_FINE` ones), so the counts and
the detail list are always mutually consistent.
"""
import argparse
import asyncio
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import AsyncSessionLocal
from app.models.enums import IngredientSource, TRUSTED_INGREDIENT_SOURCES
from app.models.ingredient import Ingredient
from app.models.product import Product
from app.repositories import ingredient_alias_repository, ingredient_repository
from app.services.ingredient_catalog import SOURCE_PRIORITY
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ingredient_segmentation import detect_ambiguous_segmentation
from app.services.ingredient_translation import translate_ingredient_tokens
from app.services.language_detection import detect_language
from app.services.translation_rejection import (
    PROVIDER_FAILURE_KEYS,
    REJECTION_KEYS,
    ProviderFailureCategory,
    TranslationRejection,
    component_outcome,
    zero_filled_counts,
)

_logger = structlog.get_logger(__name__)

CATEGORY_ALREADY_FINE = "ALREADY_FINE"
CATEGORY_TRANSLATED = "TRANSLATED"
CATEGORY_FLAGGED_UNRESOLVED_AMBIGUOUS = "FLAGGED_UNRESOLVED_AMBIGUOUS"
CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED = "FLAGGED_UNRESOLVED_TRANSLATION_FAILED"
CATEGORY_SKIPPED_TRUSTED_SOURCE = "SKIPPED_TRUSTED_SOURCE"

_ALL_CATEGORIES = (
    CATEGORY_ALREADY_FINE,
    CATEGORY_TRANSLATED,
    CATEGORY_FLAGGED_UNRESOLVED_AMBIGUOUS,
    CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED,
    CATEGORY_SKIPPED_TRUSTED_SOURCE,
)

# task requirement: never guess/apply a translation for a token whose
# segmentation is ambiguous -- reuses the exact same reason string
# `ingredient_catalog._resolve_ingredient_languages` already writes to
# `SyntheticIngredient.uncertainty_reason` for an unreliable translation,
# so a downstream reader never has to learn a second vocabulary for the
# "translation could not be verified" case.
_REASON_TRANSLATION_UNRELIABLE = "TRANSLATION_UNRELIABLE"

_PRODUCT_TEXT_PREVIEW_LEN = 200


@dataclass(frozen=True)
class IngredientRepairEntry:
    ingredient_id: str
    common_name: str
    normalized_name: str
    source: str
    detected_language: str
    category: str
    reason: str | None = None
    proposed_translation: str | None = None
    applied: bool = False
    # INTERNAL diagnostics only (see `app.services.translation_rejection`):
    # closed-vocabulary enum strings, never text. Set only for
    # `FLAGGED_UNRESOLVED_TRANSLATION_FAILED` entries.
    translation_failure_reason: str | None = None
    translation_provider_failure_category: str | None = None

    def to_dict(self) -> dict:
        return {
            "ingredientId": self.ingredient_id,
            "commonName": self.common_name,
            "normalizedName": self.normalized_name,
            "source": self.source,
            "detectedLanguage": self.detected_language,
            "category": self.category,
            "reason": self.reason,
            "proposedTranslation": self.proposed_translation,
            "applied": self.applied,
            "translationFailureReason": self.translation_failure_reason,
            "translationProviderFailureCategory": self.translation_provider_failure_category,
        }


@dataclass(frozen=True)
class ProductLanguageFlag:
    barcode: str
    detected_language: str
    raw_ingredient_text_preview: str

    def to_dict(self) -> dict:
        return {
            "barcode": self.barcode,
            "detectedLanguage": self.detected_language,
            "rawIngredientTextPreview": self.raw_ingredient_text_preview,
        }


@dataclass(frozen=True)
class RepairReport:
    mode: str
    generated_at: datetime
    ingredient_details: list[IngredientRepairEntry] = field(default_factory=list)
    product_flags: list[ProductLanguageFlag] = field(default_factory=list)

    @property
    def ingredient_counts(self) -> dict[str, int]:
        counts = Counter(entry.category for entry in self.ingredient_details)
        return {category: counts.get(category, 0) for category in _ALL_CATEGORIES}

    @property
    def translation_failure_reasons(self) -> dict[str, int]:
        """Zero-filled per-reason counts over every entry flagged
        `TRANSLATION_UNRELIABLE`; sums to
        `ingredient_counts[CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED]`
        by construction (each such entry carries exactly one reason)."""
        observed = Counter(
            entry.translation_failure_reason
            for entry in self.ingredient_details
            if entry.category == CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED
        )
        return zero_filled_counts(REJECTION_KEYS, observed)

    @property
    def translation_provider_failure_categories(self) -> dict[str, int]:
        observed = Counter(
            entry.translation_provider_failure_category
            for entry in self.ingredient_details
            if entry.category == CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED
            and entry.translation_provider_failure_category is not None
        )
        return zero_filled_counts(PROVIDER_FAILURE_KEYS, observed)

    @property
    def translation_attempt(self) -> dict:
        """Attempt/partial/final summary of this run's single batched
        translation call, derived from the entries (so it can never
        disagree with `ingredientCounts`): `targets` = entries sent,
        `reliable` = verified, `rejected` = flagged; `outcome` is
        `not_attempted` / `succeeded` / `partial` / `failed`."""
        counts = self.ingredient_counts
        reliable = counts[CATEGORY_TRANSLATED]
        rejected = counts[CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED]
        targets = reliable + rejected
        return {
            "batches": 1 if targets else 0,
            "targets": targets,
            "reliable": reliable,
            "rejected": rejected,
            "outcome": component_outcome(reliable, rejected) if targets else "not_attempted",
        }

    def to_dict(self) -> dict:
        counts = self.ingredient_counts
        return {
            "mode": self.mode,
            "generatedAt": self.generated_at.isoformat(),
            "ingredientCounts": {
                "alreadyFine": counts[CATEGORY_ALREADY_FINE],
                "translated": counts[CATEGORY_TRANSLATED],
                "flaggedUnresolvedAmbiguous": counts[CATEGORY_FLAGGED_UNRESOLVED_AMBIGUOUS],
                "flaggedUnresolvedTranslationFailed": counts[CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED],
                "skippedTrustedSource": counts[CATEGORY_SKIPPED_TRUSTED_SOURCE],
            },
            "ingredientDetails": [entry.to_dict() for entry in self.ingredient_details],
            "translationFailureReasons": self.translation_failure_reasons,
            "translationProviderFailureCategories": self.translation_provider_failure_categories,
            "translationAttempt": self.translation_attempt,
            "productFlaggedCount": len(self.product_flags),
            "productFlags": [flag.to_dict() for flag in self.product_flags],
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _register_alias(
    db: AsyncSession,
    *,
    ingredient_id: str,
    alias_text: str,
    alias_normalized: str,
    language: str | None,
    source: IngredientSource,
) -> None:
    """Best-effort alias registration -- same shape/behavior as
    `app.services.ingredient_catalog._register_original_text_alias`
    (idempotent via `IngredientAlias.alias_normalized`'s global
    uniqueness; a failure is logged and swallowed rather than aborting
    the row's own already-applied repair, and an alias that already
    points at a DIFFERENT ingredient id is left untouched -- this tool
    never reassigns/overwrites an existing alias, only ever registers a
    new one when the normalized text is not already claimed).
    """
    if not alias_normalized:
        return
    try:
        await ingredient_alias_repository.get_or_create(
            db,
            ingredient_id=ingredient_id,
            alias_text=alias_text,
            alias_normalized=alias_normalized,
            language=language,
            source=source,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("ingredient_repair_alias_registration_failed", ingredient_id=ingredient_id)


async def _apply_ingredient_translation(
    db: AsyncSession, ingredient: Ingredient, *, translated_text: str, detected_language: str
) -> None:
    """Repair ONE `Ingredient` row in a single SAVEPOINT (task
    requirement 3: transactional per row within the batch) -- see the
    module docstring's "Applying a repair" section for the exact
    ordering and why each step exists."""
    original_common_name = ingredient.common_name
    original_normalized = ingredient.normalized_name or normalize_ingredient_name(original_common_name)
    new_normalized = normalize_ingredient_name(translated_text)

    async with db.begin_nested():
        # Step 1: preserve the original text BEFORE it's overwritten.
        await _register_alias(
            db,
            ingredient_id=ingredient.id,
            alias_text=original_common_name,
            alias_normalized=original_normalized,
            language=detected_language,
            source=IngredientSource.GEMINI,
        )

        # Steps 2-3: update the canonical text and (never-downgrading) source.
        ingredient.common_name = translated_text
        ingredient.normalized_name = new_normalized
        if SOURCE_PRIORITY[IngredientSource.GEMINI] >= SOURCE_PRIORITY[ingredient.source]:
            ingredient.source = IngredientSource.GEMINI
        await db.flush()

        # Step 4: also register the NEW text as its own alias, so a
        # future scan of the same now-English text converges back onto
        # this same row instead of creating a duplicate.
        if new_normalized != original_normalized:
            await _register_alias(
                db,
                ingredient_id=ingredient.id,
                alias_text=translated_text,
                alias_normalized=new_normalized,
                language="en",
                source=IngredientSource.GEMINI,
            )


async def _collect_product_flags(db: AsyncSession) -> list[ProductLanguageFlag]:
    """Report-only (task requirement 8) -- never writes to `Product`."""
    stmt = select(Product.barcode, Product.raw_ingredient_text).order_by(Product.barcode.asc())
    rows = (await db.execute(stmt)).all()

    flags: list[ProductLanguageFlag] = []
    for barcode, raw_text in rows:
        if not raw_text or not raw_text.strip():
            continue
        lang = detect_language(raw_text)
        if lang in ("en", "bg"):
            continue
        flags.append(
            ProductLanguageFlag(
                barcode=barcode,
                detected_language=lang,
                raw_ingredient_text_preview=raw_text[:_PRODUCT_TEXT_PREVIEW_LEN],
            )
        )
    return flags


async def run_repair(db: AsyncSession, *, apply: bool) -> RepairReport:
    """The dry-run/apply entry point a caller (this module's own `main`,
    or a test) drives directly against an already-open `AsyncSession`.
    See the module docstring for the full detection/repair/report
    contract. Never raises for an individual bad row -- see
    `ingredient_translation.translate_ingredient_tokens`'s own
    graceful-degradation contract, which this function relies on rather
    than duplicating.
    """
    ingredient_rows = await ingredient_repository.get_all(db)

    entries: list[IngredientRepairEntry] = []
    to_translate: list[tuple[Ingredient, str]] = []

    for row in ingredient_rows:
        lang = detect_language(row.common_name)
        base_kwargs = dict(
            ingredient_id=row.id,
            common_name=row.common_name,
            normalized_name=row.normalized_name,
            source=row.source.value,
        )

        if lang in ("en", "bg", "unknown"):
            entries.append(IngredientRepairEntry(**base_kwargs, detected_language=lang, category=CATEGORY_ALREADY_FINE))
            continue

        if row.source in TRUSTED_INGREDIENT_SOURCES:
            entries.append(
                IngredientRepairEntry(**base_kwargs, detected_language=lang, category=CATEGORY_SKIPPED_TRUSTED_SOURCE)
            )
            continue

        reason = detect_ambiguous_segmentation(row.common_name)
        if reason is not None:
            entries.append(
                IngredientRepairEntry(
                    **base_kwargs, detected_language=lang, category=CATEGORY_FLAGGED_UNRESOLVED_AMBIGUOUS, reason=reason
                )
            )
            continue

        to_translate.append((row, lang))

    if to_translate:
        translations = await translate_ingredient_tokens([row.common_name for row, _ in to_translate])
        for (row, lang), result in zip(to_translate, translations):
            detected = result.detected_language or lang
            base_kwargs = dict(
                ingredient_id=row.id,
                common_name=row.common_name,
                normalized_name=row.normalized_name,
                source=row.source.value,
            )
            if result.reliable and result.translated_text:
                applied = False
                if apply:
                    await _apply_ingredient_translation(
                        db, row, translated_text=result.translated_text, detected_language=detected
                    )
                    applied = True
                entries.append(
                    IngredientRepairEntry(
                        **base_kwargs,
                        detected_language=detected,
                        category=CATEGORY_TRANSLATED,
                        proposed_translation=result.translated_text,
                        applied=applied,
                    )
                )
            else:
                entries.append(
                    IngredientRepairEntry(
                        **base_kwargs,
                        detected_language=detected,
                        category=CATEGORY_FLAGGED_UNRESOLVED_TRANSLATION_FAILED,
                        reason=_REASON_TRANSLATION_UNRELIABLE,
                        translation_failure_reason=(
                            result.rejection_reason.value
                            if result.rejection_reason is not None
                            else TranslationRejection.UNSPECIFIED.value
                        ),
                        # Keeps `translationProviderFailureCategories`
                        # summing to `providerUnavailable` even for a
                        # translator that gave a reason but no category.
                        translation_provider_failure_category=(
                            result.provider_failure_category.value
                            if result.provider_failure_category is not None
                            else (
                                ProviderFailureCategory.UNKNOWN.value
                                if result.rejection_reason is TranslationRejection.PROVIDER_UNAVAILABLE
                                else None
                            )
                        ),
                    )
                )

    product_flags = await _collect_product_flags(db)

    if apply:
        await db.commit()
    else:
        await db.rollback()

    return RepairReport(
        mode="apply" if apply else "dry_run",
        generated_at=_utcnow(),
        ingredient_details=entries,
        product_flags=product_flags,
    )


async def main(*, apply: bool) -> RepairReport:
    async with AsyncSessionLocal() as session:
        return await run_repair(session, apply=apply)


def _print_report(report: RepairReport) -> None:
    print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Report (default) or repair (--apply) old Ingredient/Product rows "
            "persisted before the ingredient-language pipeline existed."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write repairs. Without this flag, runs a dry-run report only (no writes).",
    )
    args = parser.parse_args()
    generated_report = asyncio.run(main(apply=args.apply))
    _print_report(generated_report)
