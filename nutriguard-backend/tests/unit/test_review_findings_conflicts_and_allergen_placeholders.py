"""
Independent-review findings on the issue #21 follow-up (both confirmed with
the inputs below and fixed):

F1  a model's explicit `true` must not survive positive incompatibility
    evidence in the same label ("gelatin, pork fat" + isVegan/isHalal true);
F2  placeholder/absence strings from Gemini or Open Food Facts ("None",
    "N/A", "No allergens", `en:none`) must never be stored as allergens.
"""
import json

from app.integrations.barcode_providers.open_food_facts import _allergens
from app.services.gemini_image_parser import _extract_allergens_text, parse_gemini_image_json_result


def _parse(**overrides):
    payload = {"productName": "X", "rawIngredientText": "gelatin, pork fat, sugar", "ingredients": []}
    payload.update(overrides)
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    return result[0]


def test_gemini_true_claims_contradicted_by_the_label_text_become_unknown():
    data = _parse(isVegan=True, isHalal=True, isKosher=True, isVegetarian=None, isGlutenFree=True)
    assert (data.is_vegan, data.is_halal, data.is_kosher) == (None, None, None)
    assert data.is_vegetarian is False  # unknown by the model, filled from the "pork"/"gelatin" evidence
    assert data.is_gluten_free is True  # uncontradicted explicit claim is kept


def test_gemini_explicit_false_is_kept():
    data = _parse(rawIngredientText="water", isVegan=False)
    assert data.is_vegan is False


def test_gemini_absence_placeholders_are_never_stored_as_allergens():
    for allergens in (["None"], ["No allergens", "N/A"], ["none"], ["null", "-"], [""], "None"):
        assert _extract_allergens_text({"allergens": allergens}) == "", allergens
        assert _parse(allergens=allergens).allergens_detected == "", allergens
    assert _extract_allergens_text({"allergens": ["none", "Milk", "milk", "Soy"]}) == "Milk, Soy"


def test_open_food_facts_absence_tags_are_not_allergens():
    assert _allergens({"allergens_tags": ["en:none"]}) == []
    assert _allergens({"allergens_tags": ["en:none", "en:milk", "en:soybeans"]}) == ["Milk", "Soybeans"]
    assert _allergens({}) == []
