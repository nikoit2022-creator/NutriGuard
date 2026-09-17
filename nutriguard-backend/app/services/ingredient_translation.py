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
from app.services.language_detection import detect_language

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
# A SHORT (<=2 word) translated result containing no non-ASCII character
# at all is used as a narrow fallback: independent, script-level
# evidence it is NOT still-untranslated foreign text (a genuine Romanian/
# Bulgarian/etc. word very often carries a diacritic or a non-Latin
# script). Deliberately bounded to <=2 words, NOT any length -- a longer
# ASCII-only phrase can still genuinely be un-translated foreign text
# (e.g. French "Lait Entier Complet" has no diacritics at all) where the
# word-count alone no longer makes coincidental false-acceptance
# unlikely; longer text always goes through the stricter word-based
# `detect_language` check only, unchanged. This is an OR alongside that
# stricter check, never instead of it.
_NON_ASCII_RE = re.compile(r"[^\x00-\x7F]")
_MAX_ASCII_FALLBACK_WORDS = 2


def _is_plain_ascii_text(text: str) -> bool:
    if _NON_ASCII_RE.search(text):
        return False
    return len(text.split()) <= _MAX_ASCII_FALLBACK_WORDS


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
    """

    original_text: str
    translated_text: str | None
    detected_language: str
    confidence: float | None
    reliable: bool


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


def _translation_is_reliable(source_text: str, translated_text: str, confidence: float) -> bool:
    """Every check is deterministic and independent of what Gemini
    itself claimed -- mirrors `label_language._verify_translation_invariants`,
    applied per-entry rather than to a whole blob (closing the gap where
    a whole-blob aggregate check can pass even though one embedded
    fragment stayed untranslated)."""
    if confidence < _MIN_TRANSLATION_CONFIDENCE or not math.isfinite(confidence):
        return False
    if is_placeholder(translated_text) or not translated_text.strip():
        return False
    if detect_language(translated_text) != "en" and not _is_plain_ascii_text(translated_text):
        return False
    if _extract_e_numbers(source_text) != _extract_e_numbers(translated_text):
        return False
    if _extract_numeric_tokens(source_text) != _extract_numeric_tokens(translated_text):
        return False
    return True


def _unreliable(original_text: str) -> IngredientTokenTranslation:
    return IngredientTokenTranslation(
        original_text=original_text,
        translated_text=None,
        detected_language="other",
        confidence=None,
        reliable=False,
    )


async def translate_ingredient_tokens(tokens: list[str]) -> list[IngredientTokenTranslation]:
    """Translate every entry in `tokens` in ONE batched Gemini call
    (full-list context). Always returns exactly `len(tokens)` results,
    in the same order -- a whole-call failure (Gemini unavailable,
    unparsable response, wrong entry count) degrades EVERY entry to
    `reliable=False` rather than raising, so one bad batch never breaks
    the scan; the caller already guarantees it only ever calls this with
    a non-empty list of tokens that passed `ingredient_segmentation.
    detect_ambiguous_segmentation` (`None`) and independently detected as
    `"other"` -- callers must not include en/bg/unknown tokens here.
    """
    if not tokens:
        return []

    try:
        raw_response = await gemini_service.translate_ingredient_list(tokens)
    except GeminiUnavailableError:
        return [_unreliable(t) for t in tokens]

    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError, ValueError):
        return [_unreliable(t) for t in tokens]

    if not isinstance(payload, list) or len(payload) != len(tokens):
        return [_unreliable(t) for t in tokens]

    results: list[IngredientTokenTranslation] = []
    for original_text, raw_entry in zip(tokens, payload):
        try:
            entry = _TranslationEntry.model_validate(raw_entry)
        except ValidationError:
            results.append(_unreliable(original_text))
            continue

        confidence = entry.confidence
        translated = entry.translatedText.strip()
        detected_language = entry.detectedLanguage.strip().lower() or "other"

        if not _translation_is_reliable(original_text, translated, confidence):
            results.append(
                IngredientTokenTranslation(
                    original_text=original_text,
                    translated_text=None,
                    detected_language=detected_language,
                    confidence=round(confidence, 3) if math.isfinite(confidence) else None,
                    reliable=False,
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
