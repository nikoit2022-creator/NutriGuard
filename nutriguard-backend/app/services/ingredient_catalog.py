"""
The persistent ingredient knowledge cache: local-first canonical-
identity resolution for an OCR/Gemini-recognized ingredient with no
curated-database match, backed by the SAME `ingredients` table used for
curated/seeded data (see `app/models/ingredient.py`'s class docstring)
plus its alias index (`app/models/ingredient_alias.py`).

Design summary (task: "persistent ingredient knowledge cache"):
  1. Local-first (requirement 1): `get_or_create_catalog_ingredient`
     always tries an official identifier, then a known alias, before
     ever creating anything new.
  2. Canonical identity (requirement 2): official identifier > alias >
     normalized name -- see `get_or_create_catalog_ingredient`.
  3. Provenance (requirement 3): every row/alias carries `source`,
     `source_record_id`/`source_url`, `retrieved_at`, `last_verified_at`,
     `confidence`, `verification_status`, `schema_version` (see
     `app/models/ingredient.py`).
  4. Unknown ingredients (requirement 4): `_build_minimal_row` never
     fills a scientific/regulatory field -- it only ever receives an
     already-empty `SyntheticIngredient` (see `ocr_normalizer`'s own
     data-quality guarantees), so there's nothing to accidentally copy.
  5. Regulatory/scientific cache + TTL (requirement 5): `is_stale`/
     `is_within_negative_cache_window`/`merge_verified_fields`.
  6. Product relationships (requirement 6): unchanged by this module --
     `Product.ingredient_ids` already stores ids only, and
     `food_analysis.fetch_ingredients_for_product` already re-reads live
     `Ingredient` rows on every request. This module just makes sure a
     given normalized name/identifier always resolves to the SAME id.
  7. ADI (requirement 7): unaffected -- `IngredientOut`'s
     `adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay` are derived at
     presentation time from the canonical row's own `acceptable_daily_intake`
     text (see `app.services.ingredient_regulatory`), never stored
     per-product.
  8. Privacy (requirement 8): nothing here ever touches an image, a
     full model prompt/response, a user id, or a health profile -- only
     ingredient name/identifier text and the provenance metadata above.
  9. Concurrency (requirement 9): `get_or_create_catalog_ingredient`
     uses the exact SAVEPOINT get-or-create pattern already established
     by `product_repository.insert_new`/`product_source_repository.record_discovery`
     (see `ingredient_repository.insert_new`/`ingredient_alias_repository.get_or_create`).

No external ingredient-lookup API exists in this codebase today (Gemini
is used only for whole-label extraction, never per-ingredient regulatory
lookup) -- `REGULATORY_LOOKUP`-sourced data, `LIMITED_DATA` status, and
`merge_verified_fields`'s confidence-gated overwrite protection are all
real, fully tested, ready seams for a FUTURE such integration, not
something faked here to justify testing them. See
`docs/CODEX_HANDOFF.md` for the full scoping note.
"""
import json
from dataclasses import dataclass
from dataclasses import replace as _dataclasses_replace
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import (
    TRUSTED_INGREDIENT_SOURCES,
    IngredientSource,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository, ingredient_repository, product_repository
from app.services.barcode_text_safety import is_placeholder
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ingredient_regulatory import is_authoritative_regulatory_source
from app.services.ingredient_segmentation import detect_ambiguous_segmentation
from app.services.ingredient_translation import translate_ingredient_tokens
from app.services.language_detection import detect_language
from app.services.ocr_normalizer import SyntheticIngredient, create_synthetic_ingredient

_logger = structlog.get_logger(__name__)

# Confidence assigned to a synthetic row built from a VERIFIED-reliable
# translation -- lower than a curated/regulatory source, higher than a
# bare untranslated OCR guess (see `_build_minimal_row`/`SOURCE_PRIORITY`
# below): real content, from a real translation, still never a
# regulatory confirmation (`IngredientSource.GEMINI` never promotes past
# `LIMITED_DATA` -- see `merge_verified_fields`).
_TRANSLATED_SYNTHETIC_CONFIDENCE_FLOOR = 0.3

# Higher number = higher priority = harder to overwrite. A curated/
# seeded row is the ground truth; nothing OCR/Gemini ever observes may
# downgrade or overwrite it (task requirement 3: "Lower-quality OCR or
# Gemini data must never overwrite curated or regulatory information").
SOURCE_PRIORITY: dict[IngredientSource, int] = {
    IngredientSource.CURATED_SEED: 100,
    IngredientSource.REGULATORY_LOOKUP: 90,
    IngredientSource.GEMINI: 50,
    IngredientSource.OCR_HEURISTIC: 10,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite (this test suite's DB) doesn't preserve timezone info on
    a `DateTime(timezone=True)` column -- a value written as UTC comes
    back naive. Every timestamp this module ever writes IS UTC (see
    `_utcnow`), so treating a naive read-back as UTC is always correct,
    never a guess. Same pattern as `auth_service`'s
    `expires_at.replace(tzinfo=timezone.utc)`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def derive_ins_number_from_e_number(e_number: str | None) -> str | None:
    """INS (Codex Alimentarius International Numbering System) code
    from an already-verified E-number. For the food additives these two
    schemes share, the EU E-number is built directly on the INS number
    (e.g. INS 951 == E951 aspartame) -- a safe, mechanical derivation
    from a genuine identifier, never a fabricated one. `None` for
    anything that isn't a plain "E" + digits (+ optional letter suffix)
    E-number."""
    if not e_number or not e_number.upper().startswith("E"):
        return None
    return e_number[1:].upper() or None


def is_stale(ingredient: Ingredient, *, now: datetime | None = None) -> bool:
    """Task requirement 5: a VERIFIED record's regulatory/scientific
    data is not cached forever. `True` once `last_verified_at` is older
    than `INGREDIENT_VERIFIED_DATA_TTL_SECONDS` -- the caller (see
    `IngredientOut.needs_refresh`) still serves the last known value
    immediately; staleness only flags it for a future refresh, it never
    blocks a scan. A row that was never verified at all (no
    `last_verified_at`) is not "stale" in this sense -- it's simply not
    verified, which `risk_assessment_available`/`verification_status`
    already communicate."""
    if ingredient.verification_status != IngredientVerificationStatus.VERIFIED:
        return False
    if ingredient.last_verified_at is None:
        return False
    now = now or _utcnow()
    age = now - _as_utc(ingredient.last_verified_at)
    return age > timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS)


def is_within_negative_cache_window(ingredient: Ingredient, *, now: datetime | None = None) -> bool:
    """Task requirement 5: a failed/empty lookup may use a short
    negative-cache TTL so the same unresolved ingredient isn't
    re-attempted for every product. `True` while a not-yet-VERIFIED
    row's `retrieved_at` is within `INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS`
    -- the gate a future external-lookup/revalidation step should check
    before spending a real network call on a record this fresh (see the
    module docstring: no such external call exists in this codebase
    today, but `get_or_create_catalog_ingredient`'s local-first
    resolution already means an UNVERIFIED row is reused as-is either
    way -- this predicate is what a future revalidation attempt would
    gate on)."""
    if ingredient.verification_status == IngredientVerificationStatus.VERIFIED:
        return False
    if ingredient.retrieved_at is None:
        return False
    now = now or _utcnow()
    age = now - _as_utc(ingredient.retrieved_at)
    return age <= timedelta(seconds=settings.INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS)


# Fields `merge_verified_fields` is allowed to touch -- deliberately
# only the scientific/regulatory ones a future regulatory-lookup
# integration would supply; identity columns (`id`, `e_number`, ...)
# and personalization-adjacent booleans (`bad_for_*`) are out of scope
# for this generic merge (see `_fill_missing_identity_fields` for the
# identity-only case this module actually exercises today).
_MERGEABLE_FIELDS = (
    "scientific_name", "category", "description", "purpose_in_food",
    "health_concerns", "evidence_level", "countries_restricted_or_banned",
    "efsa_status", "fda_status", "who_iarc_classification",
    "acceptable_daily_intake", "side_effects", "references", "risk_level",
)


def _is_blank_value(value: Any) -> bool:
    """True for `None`, `""`, or a placeholder string ("null", "n/a",
    "unknown", ...) -- the same vocabulary `barcode_text_safety.
    is_placeholder` already uses for `Product` fields. Task requirement
    5: a partial response's blank/null/placeholder field must never
    erase a meaningful existing value -- `merge_verified_fields` simply
    skips such a field rather than ever writing it, regardless of what
    the existing value already was."""
    if value is None:
        return True
    if isinstance(value, str):
        return is_placeholder(value)
    return False


def parse_field_provenance(raw: str | None) -> dict[str, dict[str, Any]]:
    """Parses `Ingredient.field_provenance_json` ONCE into a plain dict.
    Public -- a caller that needs MULTIPLE fields' sources for the SAME
    row (`IngredientOut`'s several `@computed_field`s;
    `food_analysis._ingredient_out_dict`'s equivalent hand-written
    block) should call this once and pass the result to
    `resolve_parsed_field_source` per field, rather than re-parsing the
    same small JSON string once per field (code-review efficiency
    fix -- `resolve_field_source` below still exists for a single-field
    lookup and is built on these same two functions).

    Never raises on malformed/missing input -- a corrupt or absent
    `field_provenance_json` degrades to "no per-field provenance
    recorded yet" (every field then correctly falls back to the row's
    own record-level `source`, see `resolve_parsed_field_source`),
    never to an error that would block reading an otherwise-valid
    `Ingredient` row."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _dump_field_provenance(provenance: dict[str, dict[str, Any]]) -> str:
    return json.dumps(provenance, sort_keys=True)


def _load_field_provenance(existing: Any) -> dict[str, dict[str, Any]]:
    return parse_field_provenance(getattr(existing, "field_provenance_json", None))


def resolve_parsed_field_source(
    provenance: dict[str, dict[str, Any]], field_name: str, *, fallback: IngredientSource
) -> IngredientSource:
    """`resolve_field_source`'s actual lookup, given an ALREADY-parsed
    provenance dict (see `parse_field_provenance`) -- the low-level half
    a multi-field caller should use directly to avoid re-parsing the
    same JSON string once per field."""
    entry = provenance.get(field_name)
    if not entry:
        return fallback
    try:
        return IngredientSource(entry["source"])
    except (KeyError, ValueError):
        return fallback


def resolve_field_source(
    field_provenance_json: str | None, field_name: str, *, fallback: IngredientSource
) -> IngredientSource:
    """The TRUE source that actually supplied `field_name`'s CURRENT
    value on a row -- task: "scientific/regulatory provenance
    truthful" (PR #13 review). Reads this row's own per-field
    provenance (see `Ingredient.field_provenance_json`) when
    `merge_verified_fields` has ever independently written `field_name`
    specifically; otherwise falls back to `fallback` (the row's own
    record-level `source`, accurate for any field that predates
    per-field tracking or has simply never been independently merged --
    a curated/seeded row's fields all came from one atomic INSERT, and
    a freshly get-or-created OCR stub's fields are all still blank from
    that same single write).

    Any caller gating a regulatory claim (an EFSA/FDA approval status,
    a numeric ADI figure -- see `app.services.ingredient_regulatory`)
    on trustworthiness MUST resolve the source through this function,
    never read the row's bare `source` column directly for that
    purpose -- doing so would relabel an untouched field as if the
    row's most recent partial merge had supplied it too, exactly the
    bug this function exists to close.

    A single-field convenience wrapper around
    `parse_field_provenance`/`resolve_parsed_field_source` -- a caller
    resolving MULTIPLE fields for the SAME row should call those two
    directly instead, parsing `field_provenance_json` only once.
    """
    return resolve_parsed_field_source(parse_field_provenance(field_provenance_json), field_name, fallback=fallback)


def get_field_source(ingredient: Ingredient, field_name: str) -> IngredientSource:
    """`resolve_field_source` for a real `Ingredient` ORM row."""
    return resolve_field_source(
        ingredient.field_provenance_json, field_name, fallback=ingredient.source
    )


def is_field_trustworthy(ingredient: Ingredient, field_name: str) -> bool:
    """Whether `field_name`'s CURRENT value on `ingredient` has trusted,
    VERIFIED provenance -- safe to present as an authoritative
    scientific/regulatory claim (task: "risk assessment and citation
    provenance field-specific", PR #13 review round 3). Reuses
    `ingredient_regulatory.is_authoritative_regulatory_source`'s exact
    two-part gate -- `verification_status == VERIFIED` AND the source
    is one of `TRUSTED_INGREDIENT_SOURCES` -- but resolves that source
    through `get_field_source` (this field's own true origin) rather
    than the row's blanket record-level `source`, so a field a trusted
    merge never actually touched can never inherit trust from a
    DIFFERENT field the SAME call happened to update."""
    return is_authoritative_regulatory_source(ingredient.verification_status, get_field_source(ingredient, field_name))


def _backfill_field_provenance(existing: Any, *, now: datetime) -> dict[str, dict[str, Any]]:
    """Ensures every `_MERGEABLE_FIELDS` member has an explicit
    provenance entry BEFORE `merge_verified_fields` is allowed to move
    this row's record-level `source`/`confidence` any further --
    lazily snapshots the row's CURRENT (pre-this-call)
    `source`/`confidence`/`retrieved_at` onto any field that doesn't
    already have its own entry, so those untouched fields keep
    reporting their real, original origin forever after, even once the
    record-level `source` moves on to (honestly) describe only the
    field(s) THIS call actually supplies. A no-op past the first-ever
    `merge_verified_fields` call on a given row -- every mergeable
    field already has its own entry by then."""
    provenance = _load_field_provenance(existing)
    baseline_retrieved_at = existing.retrieved_at or now
    baseline = {
        "source": existing.source.value,
        "confidence": float(existing.confidence),
        "retrievedAt": _as_utc(baseline_retrieved_at).isoformat(),
    }
    for field_name in _MERGEABLE_FIELDS:
        provenance.setdefault(field_name, dict(baseline))
    return provenance


def merge_verified_fields(
    existing: Ingredient,
    *,
    fields: dict[str, Any],
    source: IngredientSource,
    confidence: float,
    now: datetime | None = None,
) -> bool:
    """Apply `fields` (a subset of `_MERGEABLE_FIELDS`) onto `existing`
    ONLY if `source`/`confidence` outrank what's already stored, OR this
    is a trusted-source revalidation of stale data (see below) -- task
    requirement 3/5: lower-quality OCR/Gemini data must never overwrite
    curated or regulatory information. Returns whether anything was
    actually applied (a genuine field change, OR a successful stale
    revalidation with unchanged values -- see below).

    `fields` is explicitly allowed to be a PARTIAL subset (a regulatory
    response that only supplies e.g. `description`) -- this function
    never requires a complete profile. PR #13 review fix ("scientific/
    regulatory provenance truthful"): only the field(s) actually
    supplied a real (non-blank) value get attributed to `source` in
    `existing.field_provenance_json` -- an untouched field keeps
    reporting whatever source ACTUALLY wrote it, even though the row's
    own record-level `source`/`confidence` (below) may move to describe
    this call's contribution. See `resolve_field_source`, the only
    honest way to read back which source is really behind a given
    field's current value.

    Priority is source-rank first (`SOURCE_PRIORITY`), `confidence` as
    the tie-breaker within the same source rank -- a same-source
    resupply with a HIGHER confidence than what's stored may refresh
    it (e.g. a regulatory lookup revalidating its own earlier, lower-
    confidence answer), but nothing may ever cross a higher rank
    downward, regardless of confidence -- UNLESS this is a trusted-
    source stale revalidation (below).

    Trusted-source stale revalidation (task requirement 5): once a
    VERIFIED row is actually stale (`is_stale`), the normal rank/
    confidence gate above would otherwise create a real contradiction --
    a curated row IS allowed to go stale, yet a `REGULATORY_LOOKUP`
    (rank 90) could never revalidate a `CURATED_SEED` row (rank 100)
    even though both are equally TRUSTED regulatory-grade sources (see
    `app.models.enums.TRUSTED_INGREDIENT_SOURCES`), and even a
    same-rank same-confidence resupply (e.g. `CURATED_SEED` confirming
    its own earlier `CURATED_SEED` entry is still current) would be
    rejected outright by the confidence tie-breaker. Neither is a
    quality regression -- it's the SAME or an equally-trusted source
    confirming the data is still correct. So: once `existing` is stale
    AND both the incoming and existing sources are in
    `TRUSTED_INGREDIENT_SOURCES`, the rank/confidence gate is bypassed
    entirely, and:
      - identical values still count as a SUCCESSFUL merge (this
        function returns `True`) and advance `retrieved_at`/
        `last_verified_at` -- "still current as of now" is real,
        useful provenance even with nothing to change;
      - the row's own `source`/`confidence` are only actually moved to
        the (possibly lower-ranked) incoming source when incoming rank
        is at least as high, OR the content genuinely changed (an
        honest per-field-provenance call: if the values now on the row
        literally came from the lower-ranked source, the row must say
        so, never keep claiming the old, higher-ranked one) -- a pure
        rank-preserving revalidation (no content change, lower rank)
        leaves `source`/`confidence` untouched.
    A non-trusted source (GEMINI, OCR_HEURISTIC) never gets any of this
    -- staleness only ever opens the door between the two regulatory-
    grade sources, never down to an AI/OCR guess, no matter how old the
    existing data is.
    """
    now = now or _utcnow()
    incoming_rank = SOURCE_PRIORITY[source]
    existing_rank = SOURCE_PRIORITY[existing.source]

    stale_trusted_revalidation = (
        source in TRUSTED_INGREDIENT_SOURCES
        and existing.source in TRUSTED_INGREDIENT_SOURCES
        and is_stale(existing, now=now)
    )

    if incoming_rank < existing_rank and not stale_trusted_revalidation:
        return False
    if (
        not stale_trusted_revalidation
        and incoming_rank == existing_rank
        and confidence <= float(existing.confidence)
    ):
        return False

    changed = False
    touched_fields: list[str] = []
    for field_name, value in fields.items():
        if field_name not in _MERGEABLE_FIELDS:
            continue
        if _is_blank_value(value):
            continue
        touched_fields.append(field_name)
        if getattr(existing, field_name) != value:
            setattr(existing, field_name, value)
            changed = True

    if not changed and not stale_trusted_revalidation:
        return False

    # PR #13 review fix ("scientific/regulatory provenance truthful"):
    # snapshot every field's pre-existing provenance BEFORE the
    # record-level `source`/`confidence` below can move on, then record
    # THIS call's own source/confidence against only the field(s) it
    # actually supplied a real (non-blank) value for -- never the whole
    # row. This is what keeps an untouched field honestly reporting its
    # real, older origin even once a partial merge moves the record-
    # level columns -- see `Ingredient.field_provenance_json` and
    # `resolve_field_source`.
    #
    # Unconditional (PR #13 review round 3 follow-up): a
    # stale-trusted-revalidation call may reach this point with
    # `touched_fields` EMPTY (every supplied field was blank, or
    # `fields={}` entirely -- a pure "still current as of now" ping,
    # see the stale-revalidation docstring section above) and STILL go
    # on to move `existing.source`/`confidence` below (same-or-higher
    # rank). Backfilling/persisting only when `touched_fields` was
    # non-empty left exactly that case's fields unprotected: with no
    # snapshot ever taken, `resolve_field_source` would fall back to
    # the row's OWN `source`, which the very next block is about to
    # reassign to THIS call's (trusted) source -- silently making every
    # untouched field look confirmed by this revalidation too, the
    # exact bug this whole mechanism exists to prevent. Backfilling
    # every time (not just when a field was touched) closes that gap:
    # the snapshot is taken from the CURRENT, not-yet-reassigned
    # `existing.source`, so it's always correct regardless of what
    # happens below.
    field_provenance = _backfill_field_provenance(existing, now=now)
    if touched_fields:
        entry = {"source": source.value, "confidence": confidence, "retrievedAt": now.isoformat()}
        for field_name in touched_fields:
            field_provenance[field_name] = entry
    existing.field_provenance_json = _dump_field_provenance(field_provenance)

    # Record-level provenance stays honest (task requirement 5): only
    # actually move `source`/`confidence` when the incoming contribution
    # is genuinely at least as authoritative, or it changed real content
    # (see the stale-revalidation docstring section above) -- a pure
    # "still current" revalidation from a lower-ranked trusted source
    # leaves the row's recorded source/confidence exactly as they were.
    # Per-field provenance (above) is what protects any OTHER,
    # untouched field regardless of what happens to these two below.
    if incoming_rank >= existing_rank or changed:
        existing.source = source
        existing.confidence = confidence
    existing.retrieved_at = now
    # Verification promotion is deliberately conservative: an AI-
    # generated (GEMINI) claim is real data worth storing -- outranking
    # a bare OCR guess -- but it is NOT a human/regulatory confirmation.
    # Only a REGULATORY_LOOKUP (or CURATED_SEED) source may promote a
    # row's RECORD-level `verification_status` all the way to VERIFIED
    # -- letting a GEMINI-sourced merge do that would silently let an
    # AI-suggested claim look confirmed, exactly what the data-quality
    # task (commit 1d8c3d9) exists to prevent. A GEMINI-sourced merge
    # instead promotes only to LIMITED_DATA -- real content, not yet
    # confirmed. This branch is also what advances `last_verified_at`
    # on a successful stale revalidation -- both
    # `TRUSTED_INGREDIENT_SOURCES` members always satisfy this rank
    # check.
    if SOURCE_PRIORITY[source] >= SOURCE_PRIORITY[IngredientSource.REGULATORY_LOOKUP]:
        existing.verification_status = IngredientVerificationStatus.VERIFIED
        existing.last_verified_at = now
    elif source == IngredientSource.GEMINI:
        existing.verification_status = IngredientVerificationStatus.LIMITED_DATA
    # PR #13 review round 3 ("risk assessment and citation provenance
    # field-specific"): `risk_assessment_available` -- what actually
    # lets `riskLevel` start influencing the Health Score (see
    # `food_analysis._score_and_warnings`) -- must be field-specific,
    # not a side effect of ANY trusted merge regardless of which
    # field(s) it touched. Recomputed every call from `risk_level`'s
    # OWN resolved provenance (`is_field_trustworthy`, using the
    # `field_provenance_json` just written/backfilled above) rather
    # than unconditionally set `True` here -- a regulatory update of
    # only `description`/`efsa_status` must never promote `risk_level`
    # by association just because they happened to arrive in the same
    # call.
    existing.risk_assessment_available = is_field_trustworthy(existing, "risk_level")
    return True


def _fill_missing_identity_fields(existing: Ingredient, synthetic: SyntheticIngredient) -> None:
    """A later scan of an already-cached UNVERIFIED ingredient may
    recognize an official identifier the earlier observation missed
    (e.g. the E-number segment of the label was unreadable the first
    time). Only ever FILLS a currently-null identity field -- never
    overwrites one that's already set, so this can't downgrade or
    contradict an existing value, curated or not."""
    if existing.e_number is None and synthetic.e_number:
        existing.e_number = synthetic.e_number
        ins = derive_ins_number_from_e_number(synthetic.e_number)
        if existing.ins_number is None and ins:
            existing.ins_number = ins


def _build_minimal_row(synthetic: SyntheticIngredient, normalized: str) -> Ingredient:
    """Task requirement 4: a minimal observation record for future
    recognition -- normalized name, its identifier(s) when the OCR text
    genuinely contained one, and provenance. NOTHING else: `synthetic`
    is already guaranteed to carry no fabricated scientific/regulatory
    claim (see `ocr_normalizer.create_synthetic_ingredient`), so this
    function has nothing to filter -- it would be a bug in THAT
    function, not this one, if it ever did.
    """
    now = _utcnow()
    # A verified-reliable translation is real content, from a real
    # (Gemini) source -- not a bare OCR guess -- but per `SOURCE_PRIORITY`/
    # `merge_verified_fields`, GEMINI can never promote past LIMITED_DATA,
    # so this never risks a translation being presented as a scientific/
    # regulatory confirmation (task: "translation must never promote
    # scientific verification").
    was_translated = synthetic.original_text is not None
    return Ingredient(
        id=synthetic.id,
        common_name=synthetic.common_name,
        normalized_name=normalized,
        scientific_name=synthetic.scientific_name,
        identity_uncertain=synthetic.identity_uncertain,
        uncertainty_reason=synthetic.uncertainty_reason,
        e_number=synthetic.e_number,
        ins_number=derive_ins_number_from_e_number(synthetic.e_number),
        category=synthetic.category,
        description=synthetic.description,
        purpose_in_food=synthetic.purpose_in_food,
        health_concerns=synthetic.health_concerns,
        evidence_level=synthetic.evidence_level,
        countries_restricted_or_banned=synthetic.countries_restricted_or_banned,
        efsa_status=synthetic.efsa_status,
        fda_status=synthetic.fda_status,
        who_iarc_classification=synthetic.who_iarc_classification,
        acceptable_daily_intake=synthetic.acceptable_daily_intake,
        side_effects=synthetic.side_effects,
        allergens=synthetic.allergens,
        references=synthetic.references,
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.GEMINI if was_translated else IngredientSource.OCR_HEURISTIC,
        source_record_id=None,
        source_url=None,
        retrieved_at=now,
        last_verified_at=None,
        confidence=(
            max(synthetic.translation_confidence or 0.0, _TRANSLATED_SYNTHETIC_CONFIDENCE_FLOOR)
            if was_translated
            else 0.2
        ),
        schema_version=1,
        is_gluten=synthetic.is_gluten,
        is_lactose=synthetic.is_lactose,
        is_vegan=synthetic.is_vegan,
        is_vegetarian=synthetic.is_vegetarian,
        is_halal=synthetic.is_halal,
        is_kosher=synthetic.is_kosher,
        bad_for_diabetes=synthetic.bad_for_diabetes,
        bad_for_hypertension=synthetic.bad_for_hypertension,
        bad_for_kidney_disease=synthetic.bad_for_kidney_disease,
        bad_for_gout=synthetic.bad_for_gout,
        bad_for_pregnancy=synthetic.bad_for_pregnancy,
        bad_for_children=synthetic.bad_for_children,
        bad_for_high_cholesterol=synthetic.bad_for_high_cholesterol,
    )


def _is_provably_weaker_duplicate(alias_owner: Ingredient) -> bool:
    """True ONLY when `alias_owner` is UNAMBIGUOUSLY a disposable
    synthetic duplicate that an official-identifier match may safely
    absorb (PR #13 review round 3: "do not blindly transfer every
    alias from an existing curated/verified alias owner"): still
    `UNVERIFIED`, still `OCR_HEURISTIC`-sourced, AND carrying no
    official identifier of its own. Any one of those failing means
    `alias_owner` has its own independent, non-disposable identity --
    curated/verified data is never assumed to be a duplicate of
    something else no matter what alias it happens to share, and
    neither is an UNVERIFIED row that has already picked up its OWN
    distinct `e_number`/`ins_number`/`cas_number` via
    `_fill_missing_identity_fields` (a genuinely different official
    identifier than the one that won this conflict -- two real
    ingredients, not a duplicate)."""
    return (
        alias_owner.verification_status == IngredientVerificationStatus.UNVERIFIED
        and alias_owner.source == IngredientSource.OCR_HEURISTIC
        and alias_owner.e_number is None
        and alias_owner.ins_number is None
        and alias_owner.cas_number is None
    )


async def register_curated_alias(
    db: AsyncSession,
    *,
    canonical: Ingredient,
    alias_text: str,
    language: str | None,
):
    """Register one explicitly reviewed alias for a curated row.

    Seed aliases are human-reviewed identity statements.  They may
    therefore reclaim an exact normalized alias from an older, bare
    OCR stub, but never from a verified/limited-data row or from a row
    carrying its own official identifier.  Existing product ids are
    intentionally not rewritten here; `resolve_canonical_alias_owner`
    makes those historical references read through to the curated row.
    """
    normalized = normalize_ingredient_name(alias_text)
    existing = await ingredient_alias_repository.get_by_normalized(db, normalized)
    if existing is None:
        return await ingredient_alias_repository.get_or_create(
            db,
            ingredient_id=canonical.id,
            alias_text=alias_text,
            alias_normalized=normalized,
            language=language,
            source=IngredientSource.CURATED_SEED,
        )
    if existing.ingredient_id == canonical.id:
        return existing

    current_owner = await ingredient_repository.get_by_id(db, existing.ingredient_id)
    if current_owner is not None and _is_provably_weaker_duplicate(current_owner):
        existing.ingredient_id = canonical.id
        existing.alias_text = alias_text
        existing.language = language
        existing.source = IngredientSource.CURATED_SEED
        await db.flush()
    return existing


async def resolve_canonical_alias_owner(db: AsyncSession, ingredient: Ingredient) -> Ingredient:
    """Read through a historical OCR id after a curated alias reclaimed
    its name.  Strong/verified rows are always returned unchanged; only
    a provably weak OCR stub can resolve to the alias's current owner.
    """
    if not _is_provably_weaker_duplicate(ingredient):
        return ingredient
    normalized = normalize_ingredient_name(ingredient.common_name)
    alias = await ingredient_alias_repository.get_by_normalized(db, normalized)
    if alias is None or alias.ingredient_id == ingredient.id:
        return ingredient
    canonical = await ingredient_repository.get_by_id(db, alias.ingredient_id)
    return canonical or ingredient


async def _reconcile_official_identifier_conflict(
    db: AsyncSession, *, official: Ingredient, alias_owner: Ingredient
) -> Ingredient:
    """`official` was resolved via a genuine official identifier
    (E-number/INS/CAS) match -- the strongest canonical-identity proof
    this module has (task requirement 2). `alias_owner` is whatever
    OTHER row currently owns the contested normalized-name alias
    instead. PR #13 review fix ("canonical identity precedence"): an
    official identifier must always outrank a normalized-name alias
    FOR THE CURRENT OBSERVATION, so `official` is ALWAYS the return
    value here, never `alias_owner` -- but a shared/generic alias text
    is not, by itself, proof that `alias_owner` is a duplicate of
    `official`, and getting THAT wrong is exactly the review-round-3
    regression this function now guards against.

    Two genuinely different cases (`_is_provably_weaker_duplicate`):

      * `alias_owner` is UNAMBIGUOUSLY a disposable synthetic
        duplicate (still UNVERIFIED, still OCR_HEURISTIC, no official
        identifier of its own) -- e.g. an earlier scan that only ever
        saw a bare name, never an identifier. Safe to fully absorb:
        every alias it owns (the contested one AND any other name/
        spelling variant it accumulated) is repointed onto `official`,
        so every future lookup of any of those names converges on the
        actually-correct canonical row too. `alias_owner` itself is
        then deleted ONLY when that is ALSO provably safe -- never
        while any `Product.ingredient_ids` still references its id
        (deleting it would leave that product with a dangling
        ingredient reference; a redundant curated/verified re-check
        stays here too, defense in depth, even though
        `_is_provably_weaker_duplicate` already ruled that out). When
        deletion isn't safe, `alias_owner` is simply left in place --
        unreachable by name from now on, but still perfectly valid for
        whatever already references its id directly. That is the
        deterministic outcome for a referenced synthetic loser: neither
        row is touched or merged, only the alias index converges.

      * `alias_owner` is NOT provably a duplicate -- it is itself
        curated/verified data, `LIMITED_DATA`, or an UNVERIFIED row
        that already carries its OWN distinct official identifier. A
        coincidentally-shared generic alias (e.g. two genuinely
        different, independently VERIFIED additives that both happen
        to also be known by the same generic family name, each with
        its own DIFFERENT E-number) is never treated as proof they are
        the same ingredient -- doing so would silently corrupt
        `alias_owner`'s legitimate identity based on an unrelated
        match. The alias table is left COMPLETELY untouched (no
        repoint, no delete): `official` still wins for the current
        observation (the product being analyzed right now correctly
        references `official`'s id), but any THIRD, later lookup of
        that same ambiguous alias text alone (no identifier attached)
        deterministically continues to resolve to whichever row
        already legitimately owned it -- exactly as ambiguous data
        should behave: preserved, not silently overwritten by
        pretending one later match settles it.
    """
    if alias_owner.id == official.id:
        return official

    if not _is_provably_weaker_duplicate(alias_owner):
        return official

    for other_alias in await ingredient_alias_repository.list_for_ingredient(db, alias_owner.id):
        other_alias.ingredient_id = official.id
    await db.flush()

    # Defense in depth: `_is_provably_weaker_duplicate` above already
    # guarantees this, but a row this consequential (an outright
    # DELETE) is worth a second, independent check rather than relying
    # on a single gate never regressing.
    is_curated_or_verified = (
        alias_owner.verification_status == IngredientVerificationStatus.VERIFIED
        or alias_owner.source == IngredientSource.CURATED_SEED
    )
    if not is_curated_or_verified and not await product_repository.has_ingredient_reference(db, alias_owner.id):
        await ingredient_repository.delete(db, alias_owner)

    return official


async def _register_alias_and_resolve_canonical(
    db: AsyncSession,
    *,
    candidate: Ingredient,
    synthetic: SyntheticIngredient,
    normalized: str,
    owns_row: bool,
    official_identifier_match: bool = False,
    language: str | None = None,
) -> Ingredient:
    """Register `synthetic.common_name` as an alias of `candidate` and
    return whichever `Ingredient` row is ACTUALLY canonical for that
    normalized text afterward (task requirement 3: "canonical alias
    convergence"). Two concurrent calls that each propose a DIFFERENT
    ingredient for the same normalized alias -- no shared E-number, so
    the identifier lookup above can't converge them first -- must still
    return exactly one canonical `Ingredient` between them, never two.

    `ingredient_alias_repository.get_or_create` already returns the
    EXISTING alias row unchanged when one was already created (by a
    concurrent call) for this normalized text -- possibly pointing at a
    DIFFERENT `ingredient_id` than `candidate`'s. Both call sites below
    used to ignore that returned owner entirely and kept using their
    own `candidate` regardless -- exactly the bug: the loser of the
    alias race would return its own orphan row instead of the race's
    actual winner, so two different `Ingredient` rows would end up
    representing the same real-world ingredient, and two callers
    (concurrent, or a later scan resolving via alias vs. one still
    holding the loser's id) could disagree about which one is
    canonical.

    `official_identifier_match=True` only when `candidate` was resolved
    via `ingredient_repository.get_by_official_identifier` (E-number/
    INS/CAS -- always `owns_row=False` when this is set, since such a
    row is by definition a pre-existing lookup result, never something
    this call itself just inserted). PR #13 review fix ("canonical
    identity precedence"): a bare alias-table conflict is a coin flip
    between two equally-unproven candidates (handled below, unchanged),
    but an official identifier is definitive proof of identity -- it
    must win even when the alias table currently disagrees, so this
    case is handled BEFORE the generic `owns_row` race-loser logic ever
    runs (see `_reconcile_official_identifier_conflict`).

    `owns_row=True` only for a row THIS call itself just INSERTed with
    no primary-key conflict -- if it turns out to be the alias race's
    loser, it is a genuine orphan (nothing will ever be told its id) and
    is deleted here, in the SAME session/transaction, before anything
    commits, so no duplicate row is left behind. `owns_row=False` for a
    row this call merely RESOLVED to (an already-existing curated/
    previously-cached row found via E-number, or a row re-fetched after
    losing a primary-key conflict to a concurrent insert) -- never
    deleted, since it existed before this call and may already be
    relied on elsewhere.
    """
    alias = await ingredient_alias_repository.get_or_create(
        db,
        ingredient_id=candidate.id,
        alias_text=synthetic.common_name,
        alias_normalized=normalized,
        language=language,
        source=IngredientSource.GEMINI if synthetic.original_text is not None else IngredientSource.OCR_HEURISTIC,
    )
    if alias.ingredient_id == candidate.id:
        return candidate

    canonical = await ingredient_repository.get_by_id(db, alias.ingredient_id)
    if canonical is None:
        # Unreachable in practice (the alias winner would have to be
        # deleted between the get_or_create call above and this
        # lookup) -- fall back to our own row rather than returning
        # nothing; never delete `candidate` in this branch, since we
        # could not actually confirm a winner to converge onto.
        return candidate

    if official_identifier_match:
        return await _reconcile_official_identifier_conflict(db, official=candidate, alias_owner=canonical)

    if owns_row:
        await ingredient_repository.delete(db, candidate)
    return canonical


async def get_or_create_catalog_ingredient(db: AsyncSession, synthetic: SyntheticIngredient) -> Ingredient:
    """Local-first canonical-identity resolution for one OCR-recognized
    token with no curated-database match (task requirements 1 + 2):

      1. Official identifier (E-number here; INS/CAS supported by the
         lookup itself once a future source populates them) -- the
         strongest identity key.
      2. A known alias -- any previously-learned English/Bulgarian/
         spelling/OCR-variant name pointing at a canonical ingredient.
      3. Otherwise: get-or-create a minimal UNVERIFIED row, race-safe
         (requirement 9), and register this name as its first alias so
         the NEXT occurrence of the same normalized text -- from this
         request or a concurrent one -- resolves via step 2 instead of
         creating another row.

    Never treats the deterministic `synth_...` id/hash itself as proof
    of identity (requirement 2) -- it is only ever used as a primary
    key to look an ALREADY-established row back up (step 3's own
    get-or-create), never as the reason two tokens are considered the
    same ingredient; that judgment is always the identifier/alias/
    normalized-name resolution above.
    """
    normalized = normalize_ingredient_name(synthetic.common_name)
    resolved: Ingredient | None = None
    # A verified-reliable translation means `common_name` above IS now
    # genuinely English -- safe to tag its own alias "en" (task:
    # "persist reusable EN/BG display names and language-aware aliases").
    # `None` (undetermined here) for every other, untouched case.
    alias_language = "en" if synthetic.original_text is not None else None

    if synthetic.e_number:
        resolved = await ingredient_repository.get_by_official_identifier(db, e_number=synthetic.e_number)

    if resolved is None:
        alias = await ingredient_alias_repository.get_by_normalized(db, normalized)
        if alias is not None:
            resolved = await ingredient_repository.get_by_id(db, alias.ingredient_id)
            if resolved is not None:
                # This exact normalized text already has an alias --
                # nothing new to register below, return immediately.
                _fill_missing_identity_fields(resolved, synthetic)
                return resolved

    if resolved is not None:
        # Resolved via E-number (step 1), but THIS specific display text
        # has no alias of its own yet -- register it too, so a later
        # scan of the same name that DOESN'T also catch the E-number
        # (e.g. a blurrier crop) still resolves directly via alias
        # instead of needing another E-number-only lookup.
        _fill_missing_identity_fields(resolved, synthetic)
        return await _register_alias_and_resolve_canonical(
            db,
            candidate=resolved,
            synthetic=synthetic,
            normalized=normalized,
            owns_row=False,
            official_identifier_match=True,
            language=alias_language,
        )

    # Genuinely new -- race-safe get-or-create. Two concurrent scans of
    # the same never-before-seen ingredient normally compute the SAME
    # deterministic id (see `ocr_normalizer.create_synthetic_ingredient`)
    # and the same `normalized` text, so whichever one's INSERT commits
    # first wins and the loser's `insert_new` returns `None` (a plain
    # primary-key conflict on `id`) -- but two DIFFERENT display names
    # sharing the same genuine E-number (e.g. "Vitamin C (E300)" vs.
    # "Ascorbic Acid (E300)") produce DIFFERENT ids/normalized names and
    # instead conflict on the UNIQUE `e_number` column -- `id` is not
    # the only identity `insert_new` can lose a race on, so re-fetching
    # by id/alias alone is not enough; also re-check every official
    # identifier this row carries before concluding the conflict is
    # unrecoverable.
    row = _build_minimal_row(synthetic, normalized)
    inserted = await ingredient_repository.insert_new(db, row)
    # Only `True` when OUR OWN insert above actually succeeded (no
    # primary-key conflict) -- captured before any of the fallback
    # re-fetches below can reassign `inserted` to a row this call did
    # NOT create (see `_register_alias_and_resolve_canonical`'s
    # `owns_row` docstring).
    owns_row = inserted is not None
    # `True` only when the fallback chain below had to fall all the way
    # to an official-identifier re-fetch to recover from the conflict
    # (see `_register_alias_and_resolve_canonical`'s
    # `official_identifier_match` docstring) -- can only ever become
    # `True` while `owns_row` is `False` (this branch is only reached
    # when `inserted` is still `None`, i.e. our own insert already
    # lost).
    official_identifier_match = False
    if inserted is None:
        inserted = await ingredient_repository.get_by_id(db, synthetic.id)
    if inserted is None:
        alias = await ingredient_alias_repository.get_by_normalized(db, normalized)
        if alias is not None:
            inserted = await ingredient_repository.get_by_id(db, alias.ingredient_id)
    if inserted is None and (synthetic.e_number or row.ins_number or row.cas_number):
        inserted = await ingredient_repository.get_by_official_identifier(
            db, e_number=synthetic.e_number, ins_number=row.ins_number, cas_number=row.cas_number
        )
        official_identifier_match = inserted is not None
    if inserted is None:
        raise RuntimeError(
            f"Ingredient insert for id={synthetic.id!r} (e_number={synthetic.e_number!r}) "
            "conflicted but no row could be re-fetched by id, alias, or official identifier "
            "-- this should be unreachable under the documented SAVEPOINT get-or-create "
            "guarantee (see product_repository.insert_new)."
        )
    else:
        _fill_missing_identity_fields(inserted, synthetic)

    return await _register_alias_and_resolve_canonical(
        db,
        candidate=inserted,
        synthetic=synthetic,
        normalized=normalized,
        owns_row=owns_row,
        official_identifier_match=official_identifier_match,
        language=alias_language,
    )


# The finite ISO 639-1 two-letter code space -- the exact format
# `GeminiService.translate_ingredient_list`'s own prompt asks
# `detectedLanguage` to conform to (see `app.integrations.gemini`,
# `"ISO 639-1 code or short language name"`). That prompt is advisory
# only, never enforced by Gemini itself -- code-review fix: a raw
# `detectedLanguage` string is otherwise UNRESTRICTED free text (could
# be a hallucination, or injected OCR/label content reflected back by
# the model) and must never be trusted directly for a privacy-bounded
# diagnostic field. Every entry is `IngredientTokenTranslation.
# detected_language`'s only safe path into
# `IngredientTranslationSummary.detected_languages` (see
# `_normalize_detected_language_code` below) -- never rely on prompt
# wording alone for this boundary.
_ISO_639_1_CODES = frozenset(
    {
        "aa", "ab", "ae", "af", "ak", "am", "an", "ar", "as", "av", "ay", "az",
        "ba", "be", "bg", "bh", "bi", "bm", "bn", "bo", "br", "bs",
        "ca", "ce", "ch", "co", "cr", "cs", "cu", "cv", "cy",
        "da", "de", "dv", "dz",
        "ee", "el", "en", "eo", "es", "et", "eu",
        "fa", "ff", "fi", "fj", "fo", "fr", "fy",
        "ga", "gd", "gl", "gn", "gu", "gv",
        "ha", "he", "hi", "ho", "hr", "ht", "hu", "hy", "hz",
        "ia", "id", "ie", "ig", "ii", "ik", "io", "is", "it", "iu",
        "ja", "jv",
        "ka", "kg", "ki", "kj", "kk", "kl", "km", "kn", "ko", "kr", "ks", "ku", "kv", "kw", "ky",
        "la", "lb", "lg", "li", "ln", "lo", "lt", "lu", "lv",
        "mg", "mh", "mi", "mk", "ml", "mn", "mr", "ms", "mt", "my",
        "na", "nb", "nd", "ne", "ng", "nl", "nn", "no", "nr", "nv", "ny",
        "oc", "oj", "om", "or", "os",
        "pa", "pi", "pl", "ps", "pt",
        "qu",
        "rm", "rn", "ro", "ru", "rw",
        "sa", "sc", "sd", "se", "sg", "si", "sk", "sl", "sm", "sn", "so", "sq", "sr", "ss", "st",
        "su", "sv", "sw",
        "ta", "te", "tg", "th", "ti", "tk", "tl", "tn", "to", "tr", "ts", "tt", "tw", "ty",
        "ug", "uk", "ur", "uz",
        "ve", "vi", "vo",
        "wa", "wo",
        "xh",
        "yi", "yo",
        "za", "zh", "zu",
    }
)

# Defense in depth (finding: "cap lengths/counts") -- a real ISO 639-1
# code is always exactly 2 characters, so this bound is already
# structurally enforced by the length check inside
# `_normalize_detected_language_code` below; this constant only guards
# against ever slicing/logging an unbounded raw string before that
# check runs.
_MAX_RAW_LANGUAGE_LENGTH = 32

# Same "cap lengths/counts" defense in depth, for the collected SET of
# distinct languages across one request's whole ingredient list -- the
# ISO 639-1 allowlist above already bounds this to a small number in
# practice, but a pathological label with dozens of distinct foreign
# tokens should still never grow this diagnostic field unbounded.
_MAX_DETECTED_LANGUAGES = 8


def _normalize_detected_language_code(raw: str | None) -> str:
    """The ONLY safe path from Gemini's free-text `detectedLanguage`
    into a bounded diagnostic field: anything that is not, after
    stripping/lowercasing, an exact match for a real ISO 639-1 code is
    normalized to `"other"` -- never passed through. This is a
    allowlist, not a regex/shape guess: a coincidental 2-letter
    lowercase string that is NOT an actual assigned code (e.g. random
    OCR noise that happens to be 2 characters) also still normalizes to
    `"other"`, never silently accepted just because it has the right
    length."""
    if not raw:
        return "other"
    candidate = raw[:_MAX_RAW_LANGUAGE_LENGTH].strip().lower()
    if candidate in _ISO_639_1_CODES:
        return candidate
    return "other"


@dataclass(frozen=True)
class IngredientTranslationSummary:
    """The real, observed outcome of one request's per-ingredient
    translation pass (`_resolve_ingredient_languages`) -- distinct from
    the whole-LABEL-blob translation pass in `app.services.label_language`
    (a mixed label can have the whole-label pass report "ok"/no
    translation needed while individual embedded foreign tokens still
    went through this per-ingredient path -- code-review fix: this used
    to be silently discarded, so `app.api.v1.scan`'s diagnostics could
    under-report `translationAttempted` for exactly that mixed case).

    Deliberately bounded/aggregate-only -- counts and a small set of
    detected LANGUAGE CODES, never original or translated ingredient
    text -- so it is always safe to fold into request diagnostics
    without violating `app.core.scan_diagnostics`'s no-raw-content rule.
    """

    attempted: int = 0
    reliable: int = 0
    unreliable: int = 0
    detected_languages: tuple[str, ...] = ()

    def merged_with(self, other: "IngredientTranslationSummary") -> "IngredientTranslationSummary":
        """`food_analysis`'s finalize functions call `materialize_ingredients`
        TWICE per request (an earlier pass right after ingredient
        matching, then a second Bulgarian-alias-aware rebuild pass) --
        the first pass is normally where any REAL translation work
        happens; the second one's own targets are then usually already
        alias-resolved (see `_resolve_ingredient_languages` step 1) and
        contributes an empty summary. Combining both here (rather than
        the caller keeping only the second/later one) means a real
        first-pass translation is never silently dropped from
        diagnostics just because the second pass had nothing left to
        do."""
        return IngredientTranslationSummary(
            attempted=self.attempted + other.attempted,
            reliable=self.reliable + other.reliable,
            unreliable=self.unreliable + other.unreliable,
            detected_languages=tuple(
                sorted(set(self.detected_languages) | set(other.detected_languages))
            )[:_MAX_DETECTED_LANGUAGES],
        )


async def _resolve_ingredient_languages(
    db: AsyncSession, ingredients: list[Any]
) -> tuple[list[Any], bool, "IngredientTranslationSummary"]:
    """Task 1 ("consistent ingredient identity and language"): runs
    BEFORE any `SyntheticIngredient` is persisted (the root cause this
    module exists to fix -- identity used to persist untranslated
    foreign-language text into the shared catalog before any language
    policy ever ran). For each `SyntheticIngredient`:

      1. Already known? (a matching alias already exists for its exact
         text, from a PRIOR translation) -- pass through unchanged; the
         normal get-or-create/alias lookup below will resolve it for
         free, no Gemini call spent (task: "do not translate again for
         every product or request").
      2. English/Bulgarian/no usable text -- pass through unchanged
         (task: "prefer available English/Bulgarian label sections").
      3. Suspected OCR concatenation/ambiguous segmentation -- flagged
         `identity_uncertain`, NEVER sent for translation (task:
         "translation alone must not certify identity").
      4. Otherwise -- batched into ONE Gemini call across the whole list
         (full-list context); a reliably-verified translation replaces
         the synthetic ingredient (rebuilt via `create_synthetic_ingredient`
         on the ENGLISH text, so id/category/allergen-keyword detection
         are all derived correctly); an unreliable one is flagged
         `identity_uncertain` instead, original text preserved.

    Returns `(prepared_ingredients, translation_occurred, summary)` --
    the second element tells the caller whether any entry's display
    text actually changed, so `food_analysis`'s finalize functions know
    whether `Product.raw_ingredient_text` needs reconstructing from the
    materialized names to stay consistent with the ids just persisted
    (see `ocr_normalizer.reconstruct_synthetic_ingredient`, which
    re-tokenizes that exact text on every later read). `summary` is the
    real, observed outcome of THIS request's own per-ingredient
    translation attempts (distinct from the whole-label pass in
    `app.services.label_language`) -- bounded counts and detected
    language CODES only, never original/translated ingredient text, so
    it is safe to fold into request diagnostics (task: "propagate
    bounded request-scoped observed metadata from the per-ingredient
    path" / diagnostics privacy limits, see `app.core.scan_diagnostics`).
    """
    prepared: list[Any] = list(ingredients)
    to_translate: list[tuple[int, SyntheticIngredient, str]] = []

    for idx, ing in enumerate(prepared):
        if not isinstance(ing, SyntheticIngredient):
            continue
        # Already processed by an earlier pass in this same request
        # (e.g. `food_analysis`'s pre-rebuild materialize call, before
        # the rebuild's own materialize call runs on the same items).
        if ing.original_text is not None or ing.identity_uncertain:
            continue

        lang = detect_language(ing.common_name)
        if lang in ("en", "bg", "unknown"):
            continue

        existing_alias = await ingredient_alias_repository.get_by_normalized(
            db, normalize_ingredient_name(ing.common_name)
        )
        if existing_alias is not None:
            continue

        # An official identifier (E-number) is definitive proof of
        # identity on its own (task 1: "local-catalog resolution first,
        # using established identifiers and aliases") -- a foreign-
        # language OCR reading that still carries a genuine E-number
        # already identifies a KNOWN ingredient regardless of what its
        # display text says, so no translation is needed to establish
        # identity here; `get_or_create_catalog_ingredient` below
        # resolves it directly. Skipping the translation call for this
        # case also serves "do not translate again for every product or
        # request" -- an already-curated/previously-observed ingredient
        # is never worth a Gemini call just because THIS OCR reading
        # happens to be a new, foreign-language spelling of its name.
        if ing.e_number and await ingredient_repository.get_by_official_identifier(
            db, e_number=ing.e_number
        ):
            continue

        reason = detect_ambiguous_segmentation(ing.common_name)
        if reason is not None:
            prepared[idx] = _dataclasses_replace(
                ing, identity_uncertain=True, uncertainty_reason=reason, source_language=lang
            )
            continue

        to_translate.append((idx, ing, lang))

    if not to_translate:
        return prepared, False, IngredientTranslationSummary()

    # Full-list context (task requirement): every entry this scan
    # actually saw, curated and synthetic alike, not just the subset
    # that still needs translating -- a short/ambiguous target is
    # translated with its real neighboring ingredients as context, not
    # in isolation. `targets` below stays the specific subset Gemini
    # must return translations for (see `translate_ingredient_tokens`'s
    # own docstring / `GeminiService.translate_ingredient_list`).
    full_context = [getattr(item, "common_name", None) for item in prepared]
    full_context = [name for name in full_context if name]
    known_normalized_names = await ingredient_alias_repository.get_all_normalized_english(db)

    translations = await translate_ingredient_tokens(
        [ing.common_name for _, ing, _ in to_translate],
        context=full_context,
        known_normalized_names=known_normalized_names,
    )
    translation_occurred = False
    reliable_count = 0
    unreliable_count = 0
    detected_languages: set[str] = set()
    for (idx, ing, lang), result in zip(to_translate, translations):
        # Code-review fix: normalize through the finite ISO 639-1
        # allowlist BEFORE this ever reaches a diagnostic field -- see
        # `_normalize_detected_language_code`'s docstring. Gemini's raw
        # `detected_language` is untrusted free text.
        normalized_language = _normalize_detected_language_code(result.detected_language)
        if normalized_language not in ("en", "other"):
            detected_languages.add(normalized_language)
        if result.reliable and result.translated_text:
            rebuilt = create_synthetic_ingredient(result.translated_text)
            prepared[idx] = _dataclasses_replace(
                rebuilt,
                original_text=ing.common_name,
                source_language=result.detected_language or lang,
                translation_confidence=result.confidence,
            )
            translation_occurred = True
            reliable_count += 1
        else:
            prepared[idx] = _dataclasses_replace(
                ing,
                identity_uncertain=True,
                uncertainty_reason="TRANSLATION_UNRELIABLE",
                source_language=result.detected_language or lang,
            )
            unreliable_count += 1

    summary = IngredientTranslationSummary(
        attempted=len(to_translate),
        reliable=reliable_count,
        unreliable=unreliable_count,
        detected_languages=tuple(sorted(detected_languages))[:_MAX_DETECTED_LANGUAGES],
    )
    return prepared, translation_occurred, summary


async def _register_original_text_alias(
    db: AsyncSession, resolved: Ingredient, synthetic: SyntheticIngredient
) -> None:
    """After a verified translation resolves to a canonical ingredient,
    also register the ORIGINAL (pre-translation) text as its own
    language-tagged alias (task: "persist ... language-aware aliases
    where identity is established") -- so the NEXT scan of the exact
    same original-language text resolves via alias lookup alone, no
    translation call spent (see `_resolve_ingredient_languages` step 1).
    Best-effort: this is a dedup optimization for FUTURE requests, never
    required for THIS request's own correctness (the ingredient is
    already resolved by the time this runs) -- a failure here is logged
    and swallowed rather than allowed to fail the scan.
    """
    if synthetic.original_text is None:
        return
    original_normalized = normalize_ingredient_name(synthetic.original_text)
    if original_normalized == resolved.normalized_name:
        return
    try:
        await ingredient_alias_repository.get_or_create(
            db,
            ingredient_id=resolved.id,
            alias_text=synthetic.original_text,
            alias_normalized=original_normalized,
            language=synthetic.source_language,
            source=IngredientSource.GEMINI,
        )
    except Exception:  # noqa: BLE001
        _logger.warning(
            "ingredient_original_text_alias_registration_failed",
            ingredient_id=resolved.id,
        )


async def materialize_ingredients(
    db: AsyncSession, ingredients: list[Any]
) -> tuple[list[Any], bool, IngredientTranslationSummary]:
    """Replace every in-memory-only `SyntheticIngredient` in `ingredients`
    with its persisted catalog row (get-or-create, race-safe) -- called
    once, right after ingredient matching, at each of `food_analysis`'s
    scan pipelines. A curated `Ingredient` row already returned by
    `ocr_normalizer.match_against_database` passes through unchanged.

    Runs the full language-resolution pipeline (see
    `_resolve_ingredient_languages`) FIRST, so nothing foreign-language
    and untranslated/unflagged is ever persisted into the shared
    catalog. Returns `(materialized, translation_occurred, summary)` --
    see `_resolve_ingredient_languages`'s own docstring for what the
    second and third elements mean and why callers need them.
    """
    prepared, translation_occurred, summary = await _resolve_ingredient_languages(db, ingredients)

    materialized: list[Any] = []
    for ing in prepared:
        if isinstance(ing, SyntheticIngredient):
            resolved = await get_or_create_catalog_ingredient(db, ing)
            materialized.append(resolved)
            await _register_original_text_alias(db, resolved, ing)
        else:
            materialized.append(ing)
    return materialized, translation_occurred, summary
