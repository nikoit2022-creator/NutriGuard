"""
Per-ingredient-list-entry translation, batched with full-list context
(task requirement: "use the existing translation service with full-list
context and conservative validation").

Distinct from `app.services.label_language`, which translates a WHOLE
raw label-text blob (used to decide `Product.raw_ingredient_text`) --
this module translates an already-tokenized list of INDIVIDUAL
ingredient names in one Gemini call (so the model has the whole list as
context, e.g. to disambiguate a short word using its neighboring
ingredients) and validates each entry independently: one bad/low-
confidence entry never invalidates the rest of the list.

Never certifies identity -- see `app.services.ingredient_segmentation`,
which callers must run FIRST and exclude any flagged token from
translation entirely (translation alone must not certify identity for
a suspected OCR-concatenated fragment). This module's own per-entry
invariant checks (independent English re-detection, E-number/numeric-
token preservation) only prove the TRANSLATION is faithful to the
source text -- they say nothing about whether the source token was a
single, correctly-segmented ingredient name in the first place.
"""
import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services.barcode_text_safety import is_placeholder
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.language_detection import detect_language
from app.services.translation_rejection import (
    ProviderFailureCategory,
    TranslationRejection,
    provider_failure_category,
)

_MIN_TRANSLATION_CONFIDENCE = 0.55

# `language_detection.detect_language`'s "en" classification needs one
# hardcoded STRONG word or two DISTINCT WEAK words -- a threshold tuned
# for a whole label-text blob, where that much evidence is normally
# available. A single translated ingredient NAME is usually 1-2 words
# ("Milk", "Rapeseed Oil") and can genuinely be perfect English without
# ever containing one of those recognized words -- requiring
# `detect_language(...) == "en"` alone would reject a correct, verified
# translation like "MILK" -> "Milk" outright.
#
# The fallback for that case is NOT another blanket script/length rule
# (a plain-ASCII check previously used here also accepted short,
# untranslated foreign text with no diacritics, e.g. French "lait
# entier" -- a real false-accept, not a hypothetical one). Instead:
# real, pre-existing evidence -- does the translated text match a name
# ALREADY established, IN ENGLISH, in the ingredient catalog (a curated
# ingredient's own English name, or a previously-VERIFIED translation
# output -- see
# `app.repositories.ingredient_alias_repository.get_all_normalized_english`,
# which excludes every alias not explicitly tagged `language="en"`)?
# Known identity alone is not proof of output language -- a foreign
# alias (e.g. a learned French original) must never count as evidence
# an untranslated foreign RESULT is English, so only the English-tagged
# subset is ever consulted here. That is independent confirmation this
# is a genuine, known ENGLISH ingredient name, never a guess about
# scripts or word counts. A translation that is short, doesn't match a
# recognized English word, AND doesn't match any known English catalog
# name is honestly UNRESOLVED (task: "preserve an honest unresolved
# result when language cannot be established") -- rejected here, which
# the caller turns into `identity_uncertain=True` rather than a
# silently wrong guess either way.
_E_NUMBER_RE = re.compile(r"\bE[- ]?(\d{3,4}[A-Za-z]?)\b", re.IGNORECASE)
_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*(%|kcal|kj|mcg|µg|mg|kg|g|ml|l)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IngredientTokenTranslation:
    """The outcome of attempting to translate ONE ingredient-list entry.

    `reliable=False` means the translation could not be independently
    verified (Gemini unavailable, unparsable/incomplete response, low
    confidence, or a failed invariant check) -- the caller must keep the
    ORIGINAL text and mark the ingredient identity-uncertain rather than
    persist a guess. `translated_text` is `None` whenever `reliable` is
    `False`.

    `rejection_reason` / `provider_failure_category` are INTERNAL,
    diagnostics-only (see `app.services.translation_rejection`): `None`
    whenever `reliable` is `True`; otherwise the closed-vocabulary reason
    this entry was rejected (and, only for `PROVIDER_UNAVAILABLE`, the
    coarse provider sub-category). They never change which translation
    is accepted and are never persisted or exposed publicly.
    """

    original_text: str
    translated_text: str | None
    detected_language: str
    confidence: float | None
    reliable: bool
    rejection_reason: TranslationRejection | None = None
    provider_failure_category: ProviderFailureCategory | None = None


class _TranslationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    originalText: str = Field(min_length=1)
    detectedLanguage: str = Field(min_length=1, max_length=40)
    confidence: float = Field(ge=0.0, le=1.0)
    translatedText: str = Field(min_length=1)


def _normalize_number(raw: str) -> str:
    n = raw.replace(",", ".")
    if "." in n:
        n = n.rstrip("0").rstrip(".") or "0"
    return n


def _extract_e_numbers(text: str) -> Counter[str]:
    return Counter(("E" + m.group(1)).upper() for m in _E_NUMBER_RE.finditer(text))


def _extract_numeric_tokens(text: str) -> Counter[str]:
    tokens: Counter[str] = Counter()
    for match in _NUMBER_WITH_UNIT_RE.finditer(text):
        number = _normalize_number(match.group(1))
        unit = (match.group(2) or "").lower()
        tokens[f"{number}{unit}"] += 1
    return tokens


def _first_rejection(
    source_text: str,
    translated_text: str,
    confidence: float,
    *,
    known_normalized_names: frozenset[str],
) -> TranslationRejection | None:
    """Every check is deterministic and independent of what Gemini
    itself claimed -- mirrors `label_language._verify_translation_invariants`,
    applied per-entry rather than to a whole blob (closing the gap where
    a whole-blob aggregate check can pass even though one embedded
    fragment stayed untranslated).

    Returns the FIRST failing check's reason (checks run in a fixed
    order, each one an early return -- so an entry failing two checks is
    always attributed to the earlier one), or `None` when every check
    passes. `_translation_is_reliable` is `_first_rejection(...) is
    None`; splitting the checks out changes ONLY what is reported,
    never which translations are accepted.

    `known_normalized_names` -- see the module docstring's note above
    `_E_NUMBER_RE` -- is the narrow, evidence-based fallback for short
    translations `detect_language` alone can't confirm; it is never a
    substitute for `detect_language` succeeding, only an additional,
    independently-verifiable path to the same "genuinely English"
    conclusion."""
    if confidence < _MIN_TRANSLATION_CONFIDENCE or not math.isfinite(confidence):
        return TranslationRejection.LOW_CONFIDENCE
    if is_placeholder(translated_text) or not translated_text.strip():
        return TranslationRejection.EMPTY_TRANSLATION
    if (
        detect_language(translated_text) != "en"
        and normalize_ingredient_name(translated_text) not in known_normalized_names
    ):
        return TranslationRejection.LANGUAGE_REJECTED
    if _extract_e_numbers(source_text) != _extract_e_numbers(translated_text):
        return TranslationRejection.E_NUMBER_MISMATCH
    if _extract_numeric_tokens(source_text) != _extract_numeric_tokens(translated_text):
        return TranslationRejection.NUMERIC_MISMATCH
    return None


def _translation_is_reliable(
    source_text: str,
    translated_text: str,
    confidence: float,
    *,
    known_normalized_names: frozenset[str],
) -> bool:
    """Boolean wrapper over `_first_rejection` -- see its docstring."""
    return (
        _first_rejection(
            source_text, translated_text, confidence, known_normalized_names=known_normalized_names
        )
        is None
    )


def _unreliable(
    original_text: str,
    reason: TranslationRejection,
    provider_category: ProviderFailureCategory | None = None,
) -> IngredientTokenTranslation:
    return IngredientTokenTranslation(
        original_text=original_text,
        translated_text=None,
        detected_language="other",
        confidence=None,
        reliable=False,
        rejection_reason=reason,
        provider_failure_category=provider_category,
    )


def _match_responses_to_targets(
    targets: list[str], entries: list[_TranslationEntry]
) -> list[_TranslationEntry | None]:
    """Pairs each TARGET (by position in `targets`) with the response
    `_TranslationEntry` whose `originalText` exactly matches it --
    NEVER by response position/order alone (a reordered response is
    matched correctly here by identifier, not silently mismatched to
    the wrong original ingredient). Handles every irregular response
    shape safely, without ever attaching a translation (or, downstream,
    an alias) to the wrong original entry:

      - reordered response: matched by exact text, position-independent.
      - duplicate target text (the same ingredient name appears more
        than once in `targets`): each occurrence gets its OWN response
        entry, consumed in the order the response listed them for that
        text -- never the same response entry reused for two different
        target positions.
      - a response `originalText` that doesn't match any (remaining)
        target text at all -- mismatched, extra, or a hallucinated
        entry -- is silently discarded, never force-attached to some
        other target's position.
      - a target with no matching response entry at all (missing) is
        left `None` here; the caller turns that into an honest
        `_unreliable` result, never a guess.
    """
    remaining_positions: dict[str, list[int]] = {}
    for index, target_text in enumerate(targets):
        remaining_positions.setdefault(target_text, []).append(index)

    matched: list[_TranslationEntry | None] = [None] * len(targets)
    for entry in entries:
        positions = remaining_positions.get(entry.originalText)
        if not positions:
            continue
        matched[positions.pop(0)] = entry
    return matched


async def translate_ingredient_tokens(
    targets: list[str],
    *,
    context: list[str] | None = None,
    known_normalized_names: frozenset[str] = frozenset(),
) -> list[IngredientTokenTranslation]:
    """Translate every entry in `targets` in ONE batched Gemini call.
    `context` -- the FULL ingredient list this scan actually saw
    (defaults to `targets` itself when the caller has nothing broader to
    offer) -- is supplied to Gemini SEPARATELY as disambiguation
    context, with `targets` clearly identified as the entries that
    actually need a translation returned (task requirement: "supply the
    full ingredient-list context separately while clearly identifying
    the target entries" -- see `GeminiService.translate_ingredient_list`).

    Always returns exactly `len(targets)` results, in the same order.
    Every response entry is matched back to its target by its own
    `originalText` identifier (see `_match_responses_to_targets`), never
    by response position -- a whole-call failure (Gemini unavailable,
    unparsable/non-list response) degrades EVERY target to
    `reliable=False` rather than raising, so one bad batch never breaks
    the scan; the caller already guarantees it only ever calls this with
    a non-empty list of targets that passed `ingredient_segmentation.
    detect_ambiguous_segmentation` (`None`) and independently detected as
    `"other"` -- callers must not include en/bg/unknown tokens here.
    """
    if not targets:
        return []

    try:
        raw_response = await gemini_service.translate_ingredient_list(targets, context=context)
    except GeminiUnavailableError as exc:
        category = provider_failure_category(exc)
        return [_unreliable(t, TranslationRejection.PROVIDER_UNAVAILABLE, category) for t in targets]

    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [_unreliable(t, TranslationRejection.MALFORMED_RESPONSE) for t in targets]

    if not isinstance(payload, list):
        return [_unreliable(t, TranslationRejection.MALFORMED_RESPONSE) for t in targets]

    entries: list[_TranslationEntry] = []
    # Per-entry schema failures are remembered BY `originalText` (when
    # that much is still readable) so that a target whose only response
    # entry was schema-invalid is reported as `MALFORMED_RESPONSE`
    # ("the model answered, in a bad shape") rather than
    # `NO_MATCHING_ENTRY` ("the model never answered for this target") --
    # two different failure classes with different remedies. An invalid
    # entry with no readable `originalText` cannot be attributed to any
    # target, so it just leaves its target unmatched. Either way the
    # entry is discarded exactly as before (verdict unchanged).
    malformed_by_text: Counter[str] = Counter()
    for raw_entry in payload:
        try:
            entries.append(_TranslationEntry.model_validate(raw_entry))
        except ValidationError:
            # one malformed response entry never invalidates the rest
            original = raw_entry.get("originalText") if isinstance(raw_entry, dict) else None
            if isinstance(original, str):
                malformed_by_text[original] += 1
            continue

    matched = _match_responses_to_targets(targets, entries)

    results: list[IngredientTokenTranslation] = []
    for original_text, entry in zip(targets, matched):
        if entry is None:
            if malformed_by_text[original_text] > 0:
                malformed_by_text[original_text] -= 1
                results.append(_unreliable(original_text, TranslationRejection.MALFORMED_RESPONSE))
            else:
                results.append(_unreliable(original_text, TranslationRejection.NO_MATCHING_ENTRY))
            continue

        confidence = entry.confidence
        translated = entry.translatedText.strip()
        detected_language = entry.detectedLanguage.strip().lower() or "other"

        rejection = _first_rejection(
            original_text, translated, confidence, known_normalized_names=known_normalized_names
        )
        if rejection is not None:
            results.append(
                IngredientTokenTranslation(
                    original_text=original_text,
                    translated_text=None,
                    detected_language=detected_language,
                    confidence=round(confidence, 3) if math.isfinite(confidence) else None,
                    reliable=False,
                    rejection_reason=rejection,
                )
            )
            continue

        results.append(
            IngredientTokenTranslation(
                original_text=original_text,
                translated_text=translated,
                detected_language=detected_language,
                confidence=round(confidence, 3),
                reliable=True,
            )
        )

    return results
