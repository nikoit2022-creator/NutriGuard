from types import SimpleNamespace

from app.models.enums import IngredientTranslationSource, IngredientTranslationStatus
from app.services.ingredient_localization import build_localizations, canonical_text_hash


def _ingredient(**overrides):
    values = {
        "common_name": "Aspartame",
        "category": "Artificial Sweetener",
        "description": "English description",
        "purpose_in_food": "Sweetener",
        "health_concerns": "English health text",
        "evidence_level": "Moderate Evidence",
        "countries_restricted_or_banned": "",
        "efsa_status": "Authorized",
        "fda_status": "Approved",
        "acceptable_daily_intake": "0 - 40 mg/kg bw/day",
        "side_effects": "Headaches in sensitive individuals",
        "allergens": "Contains Phenylalanine",
        "localization_rows": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _bg_row(ingredient, **overrides):
    values = {
        "language": "bg",
        "common_name": "Аспартам",
        "category": "Изкуствен подсладител",
        "description": "Описание",
        "purpose_in_food": "Подсладител",
        "health_concerns": "Здравна информация",
        "evidence_level": "Умерени доказателства",
        "countries_restricted_or_banned": "",
        "efsa_status": "Разрешен",
        "fda_status": "Одобрен",
        "acceptable_daily_intake": "0–40 mg/kg телесно тегло/ден",
        "side_effects": "Главоболие при чувствителни хора",
        "allergens": "Съдържа фенилаланин",
        "translation_status": IngredientTranslationStatus.REVIEWED,
        "translation_source": IngredientTranslationSource.MACHINE_TRANSLATED,
        "source_content_hash": canonical_text_hash(ingredient),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_reviewed_current_bg_translation_is_exposed_with_canonical_english():
    ingredient = _ingredient()
    ingredient.localization_rows = [_bg_row(ingredient)]

    result = build_localizations(ingredient)

    assert result["en"]["commonName"] == "Aspartame"
    assert result["bg"]["commonName"] == "Аспартам"
    assert result["bg"]["translationStatus"] == "REVIEWED"
    assert result["bg"]["translationSource"] == "MACHINE_TRANSLATED"


def test_draft_or_stale_translation_is_hidden_and_falls_back_to_english():
    ingredient = _ingredient()
    ingredient.localization_rows = [
        _bg_row(ingredient, translation_status=IngredientTranslationStatus.DRAFT),
        _bg_row(ingredient, source_content_hash="0" * 64),
    ]

    assert build_localizations(ingredient).keys() == {"en"}


def test_localization_never_duplicates_identifiers_or_citations():
    ingredient = _ingredient()
    ingredient.localization_rows = [_bg_row(ingredient)]

    profile = build_localizations(ingredient)["bg"]

    for forbidden in ("eNumber", "insNumber", "casNumber", "references", "sourceUrl", "adiSource"):
        assert forbidden not in profile
