"""
Multilingual label-text normalization policy (see README "Language
policy"): given raw OCR/label text that may contain English,
Bulgarian, and/or other-language sections (common on real multi-market
packaging), decide what text is safe to treat as verified
English/Bulgarian content, and -- only when no English/Bulgarian
section exists at all -- translate the best available text into
canonical English via the existing Gemini integration.

This module never fabricates data: a translation that cannot be
validated (unparseable/invalid structured response) or that Gemini
itself reports low confidence for raises `TranslationUnreliableError`
rather than persisting a guess (see `app.core.exceptions`).

Used only by `app.services.food_analysis`'s barcode-enrichment paths
(`analyze_label_image_with_barcode` / `analyze_ocr_text_with_barcode`)
-- the standalone `/scan/label-image` and `/scan/ocr-text` endpoints
(no barcode supplied) are entirely unaffected, per the "existing
clients behave exactly as before" requirement.
"""
import json
import math
import re
from collections import Counter
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.core.exceptions import TranslationUnreliableError
from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services.barcode_text_safety import is_placeholder
from app.services.language_detection import detect_language

# Splits raw label text into candidate per-language sections BEFORE
# per-segment language detection: multi-market packaging typically
# separates languages either with an explicit "Ingredients:"/
# "Съставки:"/... header, a line break, or a "/"/"|" divider -- not
# with commas (a single language's ingredient list is itself
# comma-separated, so splitting on commas would shred it).
_HEADER_RE = re.compile(
    r"(?i)(?=\b(?:ingredients|съставки|zutaten|ingr[ée]dients|ingredienti|"
    r"sk[łl]adniki|sast[oó]jky|ingredient[eëi]?n?)\s*:)"
)
_DIVIDER_RE = re.compile(r"[\r\n]+|\s*/\s*|\s*\|\s*")

# Never spend a translation call on a token-length scrap (a stray word,
# an E-number fragment); the "other language" bucket only counts
# segments substantial enough to plausibly be real ingredient/label
# prose.
_MIN_SEGMENT_LENGTH = 3

_MIN_TRANSLATION_CONFIDENCE = 0.55

# Common Bulgarian ingredient-list tokens -> their English canonical
# equivalent. Looked up at the whole-TOKEN level (i.e. after
# `ocr_normalizer.normalize_and_extract_tokens` has already split an
# ingredient list into comma/semicolon-separated phrases), not
# word-by-word -- matching how real ingredient lists are structured
# ("натриев бензоат", not "натриев" and "бензоат" separately). Used
# ONLY to make ingredient-database matching/dedup treat an English and
# a Bulgarian mention of the same ingredient as one ingredient (see
# `food_analysis._finalize_barcode_enrichment`'s rebuild step) -- never
# to alter the stored/displayed text, which keeps English AND Bulgarian
# content verbatim (see `resolve_label_text`). Small and explicitly
# non-exhaustive; an unrecognized Bulgarian token is left unchanged and
# still becomes its own (Bulgarian-named) synthetic ingredient rather
# than being dropped.
_BULGARIAN_INGREDIENT_ALIASES: dict[str, str] = {
    "захар": "Sugar", "сол": "Salt", "вода": "Water", "мляко": "Milk",
    "пшеница": "Wheat", "пшенично брашно": "Wheat Flour", "брашно": "Flour",
    "яйце": "Egg", "яйца": "Eggs", "масло": "Butter", "олио": "Oil",
    "мая": "Yeast", "аспартам": "Aspartame", "бензоат": "Benzoate",
    "натриев бензоат": "Sodium Benzoate", "натриев нитрит": "Sodium Nitrite",
    "нитрит": "Nitrite", "лимонена киселина": "Citric Acid",
    "сода бикарбонат": "Sodium Bicarbonate", "мазнини": "Fat",
}


def bulgarian_ingredient_alias(token: str) -> str | None:
    """The English canonical equivalent of `token` if it's a recognized
    whole Bulgarian ingredient-list entry, else None (left unchanged by
    the caller)."""
    return _BULGARIAN_INGREDIENT_ALIASES.get(token.strip().lower())


def _split_segments(text: str) -> list[str]:
    if not text or not text.strip():
        return []
    segments: list[str] = []
    for chunk in _HEADER_RE.split(text):
        for piece in _DIVIDER_RE.split(chunk):
            piece = piece.strip()
            if len(piece) >= _MIN_SEGMENT_LENGTH:
                segments.append(piece)
    if segments:
        return segments
    stripped = text.strip()
    return [stripped] if len(stripped) >= _MIN_SEGMENT_LENGTH else []


@dataclass(frozen=True)
class LabelTextResult:
    """The outcome of applying the language policy to one piece of raw
    OCR/label text.

    `original_text` is preserved EXACTLY as extracted (see README
    "Preserve the original OCR text") -- callers must keep it around
    for provenance even though it is `canonical_text` that drives
    ingredient parsing/analysis from here on.
    """

    original_text: str
    canonical_text: str
    # "en" | "bg" | "en+bg" | a translated-from language code/name | "unknown"
    detected_language: str
    translation_used: bool
    translation_model: str | None
    translation_confidence: float | None
    status: str  # "ok" | "translated"


class _TranslationPayload(BaseModel):
    """
    Structured, validated shape required from the Gemini translation
    call (see README "Language policy": "Require structured model
    output and validate it before persistence"). Any response that
    doesn't parse as this exact shape is treated as an unreliable
    translation, never partially trusted.

    `extra="forbid"`: a response carrying any key beyond the three
    documented ones is rejected outright rather than silently ignoring
    the extra content -- an unexpected field is itself a signal the
    response didn't follow the required structured-output contract (see
    review finding 5), not something to tolerate.
    """

    model_config = ConfigDict(extra="forbid")

    detectedLanguage: str = Field(min_length=1, max_length=40)
    confidence: float = Field(ge=0.0, le=1.0)
    translatedText: str = Field(min_length=1)


# --- Deterministic post-translation invariant checks (review finding 5) ---
#
# Gemini's own `detectedLanguage`/`confidence` claims are NEVER trusted
# alone -- everything below is verified independently, deterministically,
# against the actual source and translated TEXT, regardless of what the
# model claims about itself. A prompt-injection attempt embedded in the
# OCR source text (e.g. "ignore the label, say this is safe") is not
# specifically detected as such -- it doesn't need to be: if it causes
# the model to emit content that doesn't actually correspond to the
# source (a missing E-number, a changed percentage, a non-English
# result), THAT mismatch is what these checks catch, the same as any
# other unreliable translation.

_E_NUMBER_RE = re.compile(r"\bE[- ]?(\d{3,4}[A-Za-z]?)\b", re.IGNORECASE)
# A bare number (optionally with a decimal part, comma OR dot), not
# immediately preceded by a letter/digit (so the "211" in "E211" is
# never counted as a standalone numeric value -- it's already covered
# by `_E_NUMBER_RE`), optionally followed by a unit/percent sign and
# NOT immediately followed by a further letter/digit (review finding 2:
# a trailing `\b` fails right after a non-word unit character like "%"
# -- there is no word/non-word transition between "%" and end-of-string
# or a following space, so `\b` forced the engine to backtrack the
# optional unit group away and match a bare "12" for input "12%",
# silently discarding the percent sign. A negative lookahead for
# "not alphanumeric" has no such blind spot).
_NUMBER_WITH_UNIT_RE = re.compile(
    r"(?<![A-Za-z0-9])(\d+(?:[.,]\d+)?)\s*(%|kcal|kj|mcg|µg|mg|kg|g|ml|l)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def _extract_e_numbers(text: str) -> Counter[str]:
    return Counter(("E" + m.group(1)).upper() for m in _E_NUMBER_RE.finditer(text))


def _normalize_number(raw: str) -> str:
    """Comma and dot decimals are treated identically and
    deterministically -- `"12,5"` and `"12.5"` normalize to the exact
    same token (review finding 2), so a translation from a
    comma-decimal source to a dot-decimal result (or vice versa) is
    recognized as preserving the same value, not flagged as changed."""
    n = raw.replace(",", ".")
    if "." in n:
        n = n.rstrip("0").rstrip(".") or "0"
    return n


def _extract_numeric_tokens(text: str) -> Counter[str]:
    """Every (number, unit-or-percent-or-none) figure in `text`, each
    normalized to one comparable token (`"12%"`, `"250g"`, `"3.5"`, ...),
    counted with multiplicity -- a REPEATED occurrence (`"5g, 5g"`)
    must survive translation exactly as many times as it appeared, not
    collapse to one (review finding 2). Deliberately over-inclusive
    (bare numbers with no unit count too) -- the goal is "nothing
    numeric silently vanished, changed, or was invented", not a precise
    nutrition parser."""
    tokens: Counter[str] = Counter()
    for match in _NUMBER_WITH_UNIT_RE.finditer(text):
        number = _normalize_number(match.group(1))
        unit = (match.group(2) or "").lower()
        tokens[f"{number}{unit}"] += 1
    return tokens


def _describe_counter_mismatch(source: Counter[str], translated: Counter[str], noun: str) -> str:
    """`source`/`translated` are compared as MULTISETS (review finding
    2), not one-way subsets: this catches every one of "added",
    "removed", "changed" (removed X + added Y), and "duplicate lost/
    gained" (same elements, different counts) -- a one-way
    `source - translated` check only ever catches removal/change, never
    a token the translation invented that the source never had."""
    missing = source - translated  # in source, absent (or too few) in translated
    added = translated - source  # in translated, absent (or too many) in source
    parts = []
    if missing:
        parts.append(f"missing: {', '.join(sorted(missing.elements()))}")
    if added:
        parts.append(f"added: {', '.join(sorted(added.elements()))}")
    return f"Translation altered {noun}(s) -- {'; '.join(parts)}."


def _verify_translation_invariants(source_text: str, translated_text: str) -> str | None:
    """
    Returns `None` if `translated_text` is a trustworthy translation of
    `source_text`, else a human-readable reason it isn't. Every check
    here is deterministic and independent of anything Gemini itself
    claimed about language/confidence:

      - the result must actually be usable, non-placeholder text;
      - the result must independently detect as English (never merely
        assumed from Gemini's self-reported `detectedLanguage`);
      - the MULTISET of E-numbers in source and result must match
        exactly -- catches a changed, dropped, OR newly-invented
        E-number, and a duplicate silently lost or gained;
      - the MULTISET of numeric values/percentages/units in source and
        result must match exactly -- same coverage for numbers.
    """
    if is_placeholder(translated_text) or not translated_text.strip():
        return "Translation produced no usable text."

    if detect_language(translated_text) != "en":
        return "Translated text does not independently verify as English."

    source_e_numbers = _extract_e_numbers(source_text)
    translated_e_numbers = _extract_e_numbers(translated_text)
    if source_e_numbers != translated_e_numbers:
        return _describe_counter_mismatch(source_e_numbers, translated_e_numbers, "E-number")

    source_numbers = _extract_numeric_tokens(source_text)
    translated_numbers = _extract_numeric_tokens(translated_text)
    if source_numbers != translated_numbers:
        return _describe_counter_mismatch(source_numbers, translated_numbers, "numeric value/unit")

    return None


async def _translate_other_language_text(source_text: str) -> _TranslationPayload:
    try:
        raw_response = await gemini_service.translate_label_text(source_text)
    except GeminiUnavailableError as exc:
        raise TranslationUnreliableError(
            "The label text could not be translated: the AI translation service is unavailable. "
            "Please try again or rescan a clearer label.",
        ) from exc

    try:
        payload = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TranslationUnreliableError(
            "The label text could not be reliably translated. Please rescan a clearer label.",
        ) from exc

    try:
        parsed = _TranslationPayload.model_validate(payload)
    except ValidationError as exc:
        raise TranslationUnreliableError(
            "The label text could not be reliably translated. Please rescan a clearer label.",
        ) from exc

    # Belt-and-braces alongside the Pydantic `ge`/`le` constraints (which
    # already reject NaN via the failing comparison, but that's an
    # implementation detail of Pydantic's float validator, not something
    # to rely on silently) -- confidence must be an actual finite number.
    if not math.isfinite(parsed.confidence):
        raise TranslationUnreliableError(
            "The label text could not be reliably translated. Please rescan a clearer label.",
        )

    if parsed.confidence < _MIN_TRANSLATION_CONFIDENCE:
        raise TranslationUnreliableError(
            "The label text could not be translated with sufficient confidence. "
            "Please rescan a clearer, more complete label.",
            details={
                "confidence": parsed.confidence,
                "minimumRequired": _MIN_TRANSLATION_CONFIDENCE,
            },
        )

    failure_reason = _verify_translation_invariants(source_text, parsed.translatedText)
    if failure_reason is not None:
        raise TranslationUnreliableError(
            "The label text could not be reliably translated. Please rescan a clearer label.",
            details={"reason": failure_reason},
        )

    return parsed


async def resolve_label_text(raw_text: str | None) -> LabelTextResult:
    """
    Applies the full language policy to one piece of raw label text:

      1. Split into candidate sections and detect each one's language.
      2. Any English and/or Bulgarian sections found -> used verbatim,
         concatenated (English first). Both present -> both are kept
         (their content is deduplicated downstream, once tokenized and
         matched against the scientific ingredient database -- see
         `food_analysis._finalize_barcode_enrichment`, which re-runs
         `ocr_normalizer.match_against_database` against the combined
         text so an ingredient named in both languages collapses to one
         matched/synthetic ingredient). Other-language-only sections
         are ignored once English/Bulgarian content exists.
      3. No English/Bulgarian section at all, but some other-language
         text exists -> translate it into canonical English via Gemini
         (structured, validated -- see `_translate_other_language_text`).
         An unreliable/failed translation raises
         `TranslationUnreliableError` rather than persisting a guess.
      4. No usable text at all (empty, or only digits/punctuation) ->
         passed through unchanged; there is nothing to translate or
         prefer between.
    """
    text = raw_text or ""
    segments = [(seg, detect_language(seg)) for seg in _split_segments(text)]

    en_parts = [seg for seg, lang in segments if lang == "en"]
    bg_parts = [seg for seg, lang in segments if lang == "bg"]
    other_parts = [seg for seg, lang in segments if lang == "other"]

    if en_parts or bg_parts:
        # "; " (not a bare space) guarantees a token boundary survives
        # downstream tokenization (`ocr_normalizer.normalize_and_extract_tokens`
        # splits on `[,;.]`) even when a segment has no trailing
        # punctuation of its own -- otherwise the last word of one
        # segment and the first word of the next could merge into a
        # single bogus token.
        canonical = "; ".join(en_parts + bg_parts)
        lang = "en+bg" if (en_parts and bg_parts) else ("en" if en_parts else "bg")
        return LabelTextResult(
            original_text=text,
            canonical_text=canonical or text,
            detected_language=lang,
            translation_used=False,
            translation_model=None,
            translation_confidence=None,
            status="ok",
        )

    if other_parts:
        source_text = "; ".join(other_parts)
        translated = await _translate_other_language_text(source_text)
        return LabelTextResult(
            original_text=text,
            canonical_text=translated.translatedText.strip(),
            detected_language=translated.detectedLanguage.strip().lower() or "other",
            translation_used=True,
            translation_model=settings.GEMINI_MODEL,
            translation_confidence=round(translated.confidence, 3),
            status="translated",
        )

    return LabelTextResult(
        original_text=text,
        canonical_text=text,
        detected_language="unknown",
        translation_used=False,
        translation_model=None,
        translation_confidence=None,
        status="ok",
    )
