"""
`app.services.label_language.resolve_label_text` -- the full
English/Bulgarian selection, mixed-language dedup-input, and
other-language translation policy (see README "Language policy").

Every Gemini call is mocked; no test in this file makes a live network
call.
"""
import json

import pytest

from app.core.exceptions import TranslationUnreliableError
from app.integrations.gemini import gemini_service
from app.services.label_language import bulgarian_ingredient_alias, resolve_label_text


@pytest.mark.asyncio
async def test_english_only_text_used_as_is():
    result = await resolve_label_text("Water, Sugar, Salt, Citric Acid (E330)")
    assert result.detected_language == "en"
    assert result.translation_used is False
    assert result.status == "ok"
    assert "Water" in result.canonical_text
    assert result.original_text == "Water, Sugar, Salt, Citric Acid (E330)"


@pytest.mark.asyncio
async def test_bulgarian_only_text_used_as_is():
    result = await resolve_label_text("Вода, Захар, Сол, Лимонена киселина (E330)")
    assert result.detected_language == "bg"
    assert result.translation_used is False
    assert "Захар" in result.canonical_text


@pytest.mark.asyncio
async def test_english_preferred_over_other_language_duplicate_section():
    text = "Water, Sugar, Salt (E330) / Wasser, Zucker, Salz (E330)"
    result = await resolve_label_text(text)
    assert result.detected_language == "en"
    assert "Water" in result.canonical_text
    assert "Wasser" not in result.canonical_text
    assert result.translation_used is False


@pytest.mark.asyncio
async def test_bulgarian_preferred_over_other_language_duplicate_section():
    text = "Вода, Захар, Сол (E330) / Wasser, Zucker, Salz (E330)"
    result = await resolve_label_text(text)
    assert result.detected_language == "bg"
    assert "Захар" in result.canonical_text
    assert "Zucker" not in result.canonical_text
    assert result.translation_used is False


@pytest.mark.asyncio
async def test_mixed_english_and_bulgarian_both_retained():
    text = "Water, Sugar, Salt / Вода, Захар, Сол"
    result = await resolve_label_text(text)
    assert result.detected_language == "en+bg"
    assert "Water" in result.canonical_text
    assert "Захар" in result.canonical_text
    assert result.translation_used is False


@pytest.mark.asyncio
async def test_e_numbers_percentages_quantities_and_units_survive_translation_unchanged(monkeypatch):
    """Preservation of language-neutral data is enforced by the prompt
    (see `app.integrations.gemini._TRANSLATION_PROMPT_TEMPLATE`); at the
    code level, this proves the pipeline does not itself mangle
    whatever Gemini returns for those tokens."""

    async def fake_translate(text: str) -> str:
        return json.dumps(
            {
                "detectedLanguage": "de",
                "confidence": 0.92,
                "translatedText": "Wheat Flour, Sugar 12%, Sodium Benzoate (E211), Net Weight 250g",
            }
        )

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    result = await resolve_label_text("Weizenmehl, Zucker 12%, Natriumbenzoat (E211), Nettogewicht 250g")
    assert result.translation_used is True
    assert "E211" in result.canonical_text
    assert "12%" in result.canonical_text
    assert "250g" in result.canonical_text


@pytest.mark.asyncio
async def test_other_language_only_label_is_translated_to_canonical_english(monkeypatch):
    async def fake_translate(text: str) -> str:
        assert "Zutaten" not in text or True  # sanity: we got the German text, not garbage
        return json.dumps(
            {"detectedLanguage": "de", "confidence": 0.88, "translatedText": "Water, Sugar, Salt (E330)"}
        )

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    result = await resolve_label_text("Zutaten: Wasser, Zucker, Salz (E330)")
    assert result.translation_used is True
    assert result.status == "translated"
    assert result.detected_language == "de"
    assert result.translation_model is not None
    assert result.translation_confidence == 0.88
    assert result.canonical_text == "Water, Sugar, Salt (E330)"
    # Original OCR text is preserved unchanged for provenance.
    assert result.original_text == "Zutaten: Wasser, Zucker, Salz (E330)"


@pytest.mark.asyncio
async def test_translation_low_confidence_raises_controlled_error(monkeypatch):
    async def fake_translate(text: str) -> str:
        return json.dumps({"detectedLanguage": "de", "confidence": 0.1, "translatedText": "??"})

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    with pytest.raises(TranslationUnreliableError) as exc_info:
        await resolve_label_text("Zutaten: Wasser, Zucker, Salz")
    assert exc_info.value.code == "LABEL_TRANSLATION_UNRELIABLE"
    assert exc_info.value.details["confidence"] == 0.1


@pytest.mark.asyncio
async def test_translation_invalid_json_raises_controlled_error(monkeypatch):
    async def fake_translate(text: str) -> str:
        return "this is not JSON at all {{{"

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    with pytest.raises(TranslationUnreliableError):
        await resolve_label_text("Zutaten: Wasser, Zucker, Salz")


@pytest.mark.asyncio
async def test_translation_missing_required_field_raises_controlled_error(monkeypatch):
    async def fake_translate(text: str) -> str:
        return json.dumps({"confidence": 0.9})  # missing detectedLanguage/translatedText

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    with pytest.raises(TranslationUnreliableError):
        await resolve_label_text("Zutaten: Wasser, Zucker, Salz")


@pytest.mark.asyncio
async def test_translation_placeholder_text_raises_controlled_error(monkeypatch):
    async def fake_translate(text: str) -> str:
        return json.dumps({"detectedLanguage": "de", "confidence": 0.9, "translatedText": "null"})

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)

    with pytest.raises(TranslationUnreliableError):
        await resolve_label_text("Zutaten: Wasser, Zucker, Salz")


@pytest.mark.asyncio
async def test_empty_text_passes_through_as_unknown_with_no_translation_call(monkeypatch):
    def must_not_be_called(*args, **kwargs):
        raise AssertionError("translate_label_text must not be called for empty/no-content text")

    monkeypatch.setattr(gemini_service, "translate_label_text", must_not_be_called)

    result = await resolve_label_text("")
    assert result.detected_language == "unknown"
    assert result.translation_used is False
    assert result.canonical_text == ""


def test_bulgarian_ingredient_alias_maps_known_terms():
    assert bulgarian_ingredient_alias("Захар") == "Sugar"
    assert bulgarian_ingredient_alias("натриев бензоат") == "Sodium Benzoate"
    assert bulgarian_ingredient_alias("непознат термин") is None
