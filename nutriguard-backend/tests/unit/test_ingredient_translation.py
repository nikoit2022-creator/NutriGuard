"""
`app.services.ingredient_translation.translate_ingredient_tokens` --
batched, per-entry-validated ingredient-token translation. No DB, no
network: every Gemini call is mocked (`gemini_service.
translate_ingredient_list`, monkeypatched exactly like
`tests/unit/test_label_language.py` mocks `translate_label_text`).

Most translated text used in these fakes is phrased with 2+ recognized
English "weak" words (see `app.services.language_detection`'s own
WEAK_WORDS lists) so it passes `detect_language` outright. See
`test_short_ascii_translation_is_reliable_even_without_recognized_vocabulary`
and `test_longer_ascii_only_non_english_text_is_still_marked_unreliable`
below for the narrow, bounded ASCII-script fallback
`_translation_is_reliable` uses for SHORT (<=2 word) results that
`detect_language` alone would otherwise wrongly reject (e.g. "MILK" ->
"Milk") -- and why that fallback does NOT extend to longer text.
"""
import json

import pytest

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.services.ingredient_translation import IngredientTokenTranslation, translate_ingredient_tokens


def _fake_translate(entries: list[dict]):
    """`entries`: list of raw dicts (already in the exact
    Gemini-response shape) returned verbatim, in order -- lets tests
    build deliberately malformed/partial payloads directly."""

    async def _fake(tokens: list[str]) -> str:
        return json.dumps(entries)

    return _fake


def _entry(original: str, translated: str, *, confidence: float = 0.9, detected: str = "ro") -> dict:
    return {
        "originalText": original,
        "detectedLanguage": detected,
        "confidence": confidence,
        "translatedText": translated,
    }


# --- Happy path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_token_list_returns_empty_list_without_calling_gemini(monkeypatch):
    async def must_not_be_called(tokens):
        raise AssertionError("translate_ingredient_list must not be called for an empty token list")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", must_not_be_called)
    assert await translate_ingredient_tokens([]) == []


@pytest.mark.asyncio
async def test_successful_batch_translation_all_entries_reliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                _entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.91),
                _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.88),
            ]
        ),
    )

    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])

    assert len(results) == 2
    assert results[0] == IngredientTokenTranslation(
        original_text="Ulei de rapiță",
        translated_text="Oil Made From Rapeseed",
        detected_language="ro",
        confidence=0.91,
        reliable=True,
    )
    assert results[1].reliable is True
    assert results[1].translated_text == "Made With Poppy Seeds"


@pytest.mark.asyncio
async def test_e_numbers_and_numeric_tokens_preserved_through_translation(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Zahar 12%", "Sugar Made From 12% Beet", confidence=0.8)]),
    )
    results = await translate_ingredient_tokens(["Zahar 12%"])
    assert results[0].reliable is True
    assert "12%" in results[0].translated_text


# --- Whole-batch failure: every entry degrades, never raises ---------------


@pytest.mark.asyncio
async def test_gemini_unavailable_degrades_every_entry_to_unreliable(monkeypatch):
    async def raise_unavailable(tokens):
        raise GeminiUnavailableError("not configured")

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", raise_unavailable)

    results = await translate_ingredient_tokens(["Ulei de rapiță", "Zahar"])
    assert len(results) == 2
    for r in results:
        assert r.reliable is False
        assert r.translated_text is None
        assert r.confidence is None


@pytest.mark.asyncio
async def test_unparsable_json_degrades_every_entry_to_unreliable(monkeypatch):
    async def bad_json(tokens):
        return "this is not json"

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", bad_json)

    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert len(results) == 1
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_non_list_json_payload_degrades_every_entry_to_unreliable(monkeypatch):
    async def object_not_list(tokens):
        return json.dumps({"not": "a list"})

    monkeypatch.setattr(gemini_service, "translate_ingredient_list", object_not_list)

    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_wrong_entry_count_degrades_every_entry_to_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "Oil Made From Rapeseed")]),  # only 1, for 2 tokens
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Zahar"])
    assert len(results) == 2
    for r in results:
        assert r.reliable is False


# --- Per-entry validation: one bad entry never invalidates the rest --------


@pytest.mark.asyncio
async def test_one_malformed_entry_does_not_invalidate_the_rest_of_the_batch(monkeypatch):
    """`_TranslationEntry` is `extra="forbid"` -- an entry carrying an
    unexpected extra key is rejected, but siblings in the SAME batch
    response must still be processed independently."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [
                {**_entry("Ulei de rapiță", "Oil Made From Rapeseed"), "unexpectedField": "x"},
                _entry("Semințe de mac", "Made With Poppy Seeds", confidence=0.85),
            ]
        ),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].reliable is False
    assert results[1].reliable is True
    assert results[1].translated_text == "Made With Poppy Seeds"


@pytest.mark.asyncio
async def test_missing_required_field_marks_only_that_entry_unreliable(monkeypatch):
    broken_entry = {"originalText": "Ulei de rapiță", "detectedLanguage": "ro", "confidence": 0.9}
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([broken_entry, _entry("Semințe de mac", "Made With Poppy Seeds")]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță", "Semințe de mac"])
    assert results[0].reliable is False
    assert results[1].reliable is True


# --- Per-entry invariant checks ---------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_entry_is_marked_unreliable_but_confidence_is_still_reported(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "Oil Made From Rapeseed", confidence=0.4)]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False
    assert results[0].translated_text is None
    assert results[0].confidence == 0.4  # reported for diagnostics even though unreliable


@pytest.mark.asyncio
async def test_placeholder_translated_text_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Ulei de rapiță", "N/A", confidence=0.95)]),
    )
    results = await translate_ingredient_tokens(["Ulei de rapiță"])
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_translated_text_not_independently_english_is_marked_unreliable(monkeypatch):
    """Gemini claims a confident, "en"-labeled translation, but the
    translated text itself is still French -- the independent
    `detect_language` re-check (never merely trusting Gemini's own
    self-reported `detectedLanguage`) must catch this."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Lait entier", "Lait Entier Complet", confidence=0.95, detected="en")]),
    )
    results = await translate_ingredient_tokens(["Lait entier"])
    assert results[0].reliable is False


@pytest.mark.asyncio
async def test_e_number_mismatch_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate(
            [_entry("Benzoat de sodiu (E211)", "Sodium Benzoate, Made From Salt", confidence=0.9)]
        ),
    )
    results = await translate_ingredient_tokens(["Benzoat de sodiu (E211)"])
    assert results[0].reliable is False  # E211 silently dropped by the "translation"


@pytest.mark.asyncio
async def test_numeric_token_mismatch_is_marked_unreliable(monkeypatch):
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Zahar 12%", "Sugar Made From 15% Beet", confidence=0.9)]),
    )
    results = await translate_ingredient_tokens(["Zahar 12%"])
    assert results[0].reliable is False  # 12% silently changed to 15%


# --- Short-translation ASCII fallback (bounded, task requirement) ----------


@pytest.mark.asyncio
async def test_short_ascii_translation_is_reliable_even_without_recognized_vocabulary(monkeypatch):
    """`_translation_is_reliable` requires `detect_language(translated_text)
    == "en"` OR (for a SHORT <=2-word result) that it's plain ASCII.
    `app.services.language_detection.detect_language` alone requires
    either one STRONG English word or 2 DISTINCT WEAK English words
    before calling anything "en" -- a threshold tuned for a whole
    label-text BLOB, not a single short ingredient NAME. Without the
    ASCII fallback, a 100% correct, high-confidence, exact round-trip
    single word (e.g. "MILK" -> "Milk") would be wrongly rejected --
    directly undermining the task's own single-ingredient-name
    translation goal. The fallback fixes exactly this case."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("MILK", "Milk", confidence=0.97, detected="en")]),
    )
    results = await translate_ingredient_tokens(["MILK"])
    assert results[0].reliable is True
    assert results[0].translated_text == "Milk"


@pytest.mark.asyncio
async def test_longer_ascii_only_non_english_text_is_still_marked_unreliable(monkeypatch):
    """The ASCII fallback is deliberately bounded to <=2 words -- a
    longer ASCII-only phrase can still genuinely be untranslated foreign
    text (French "Lait Entier Complet" has no diacritics at all), so it
    must fall through to the stricter word-based `detect_language` check
    only, same as before this fallback existed."""
    monkeypatch.setattr(
        gemini_service,
        "translate_ingredient_list",
        _fake_translate([_entry("Lait entier", "Lait Entier Complet", confidence=0.95, detected="en")]),
    )
    results = await translate_ingredient_tokens(["Lait entier"])
    assert results[0].reliable is False
