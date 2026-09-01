import json

from app.services.gemini_image_parser import nutrition_fields_present, parse_gemini_image_json_result


def test_valid_json_uses_gemini_nutrition_and_flags_directly():
    payload = {
        "productName": "Diet Cola",
        "brand": "Acme Beverages",
        "sugarGrams": 0.0,
        "sodiumMg": 15.0,
        "saturatedFatGrams": 0.0,
        "hasArtificialSweeteners": True,
        "hasPreservatives": False,
        "isGlutenFree": True,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        "rawIngredientText": "Carbonated Water, Aspartame (E951), Caffeine",
        "ingredients": [
            {"commonName": "Aspartame", "eNumber": "E951"},
            {"commonName": "Caffeine", "eNumber": None},
        ],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, ingredients = result

    # Every Gemini field is preserved verbatim -- nothing discarded.
    assert data.product_name == "Diet Cola"
    assert data.brand == "Acme Beverages"
    assert data.sugar_grams == 0.0
    assert data.sodium_mg == 15.0
    assert data.saturated_fat_grams == 0.0
    assert data.has_artificial_sweeteners is True
    assert data.has_preservatives is False
    assert data.nova_group == 4
    assert data.raw_ingredient_text == "Carbonated Water, Aspartame (E951), Caffeine"

    ids = [i.id for i in ingredients]
    assert len(ingredients) == 2
    assert any(i.startswith("synth_") for i in ids)  # Caffeine has no DB match


def test_gemini_ingredient_matches_scientific_database_when_present():
    class FakeDbIngredient:
        id = "e951_aspartame"
        common_name = "Aspartame"
        scientific_name = "L-alpha-aspartyl..."
        e_number = "E951"

    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Aspartame (E951)",
        "ingredients": [{"commonName": "Aspartame", "eNumber": "E951"}],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [FakeDbIngredient()])
    assert result is not None
    _, ingredients = result
    assert len(ingredients) == 1
    assert ingredients[0] is not None
    assert ingredients[0].id == "e951_aspartame"  # DB ingredient used, not a synthetic one


def test_missing_ingredients_array_falls_back_to_tokenizing_raw_text():
    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Water, Salt, Sugar",
        # no "ingredients" key at all
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    _, ingredients = result
    assert len(ingredients) == 3


def test_unusable_structured_ingredients_fall_back_to_tokenizing_raw_text():
    """A valid image result must not persist an empty ingredient set just
    because Gemini used an unexpected object shape for ingredients[]."""
    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Water, Salt",
        "ingredients": [{"name": "Water"}, {"name": "Salt"}],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    _, ingredients = result
    assert [ingredient.id for ingredient in ingredients] == ["synth_water", "synth_salt"]


def test_missing_product_name_returns_none():
    payload = {"rawIngredientText": "Water, Salt"}
    assert parse_gemini_image_json_result(json.dumps(payload), []) is None


def test_missing_raw_ingredient_text_returns_none():
    payload = {"productName": "Test Product"}
    assert parse_gemini_image_json_result(json.dumps(payload), []) is None


def test_invalid_json_returns_none():
    assert parse_gemini_image_json_result("not valid json {{{", []) is None


def test_non_object_json_returns_none():
    assert parse_gemini_image_json_result("[1, 2, 3]", []) is None


def test_missing_optional_fields_use_sane_defaults():
    payload = {"productName": "Minimal Product", "rawIngredientText": "Water"}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.brand == "Analyzed Brand"
    assert data.nova_group == 3
    assert data.sugar_grams == 0.0
    assert data.has_artificial_sweeteners is False
    # Safe-default rule (review finding 1): an unknown dietary flag must
    # default to False, never True -- see the dedicated
    # test_dietary_flag_safe_defaults_* tests below for full coverage.
    assert data.is_vegan is False


# --- Safe dietary-flag defaults (review finding 1) -------------------------
#
# isGlutenFree/isLactoseFree/isVegan/isVegetarian/isHalal/isKosher must
# default to False for anything that isn't an explicit, reliable `true`
# -- missing key, JSON null, a malformed (non-bool) value, or an
# explicit `false` all read as False; only an explicit `true` reads as
# True. A positive dietary/religious certification claim must never be
# presented on missing or ambiguous data.

_DIETARY_FLAG_KEYS = {
    "isGlutenFree": "is_gluten_free",
    "isLactoseFree": "is_lactose_free",
    "isVegan": "is_vegan",
    "isVegetarian": "is_vegetarian",
    "isHalal": "is_halal",
    "isKosher": "is_kosher",
}


def _parse_with_single_flag(key: str, value) -> object:
    payload = {"productName": "Test Product", "rawIngredientText": "Water"}
    if value is not _MISSING:
        payload[key] = value
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    return data


_MISSING = object()


def test_dietary_flag_safe_default_when_key_missing():
    for json_key, attr in _DIETARY_FLAG_KEYS.items():
        data = _parse_with_single_flag(json_key, _MISSING)
        assert getattr(data, attr) is False, json_key


def test_dietary_flag_safe_default_when_null():
    for json_key, attr in _DIETARY_FLAG_KEYS.items():
        data = _parse_with_single_flag(json_key, None)
        assert getattr(data, attr) is False, json_key


def test_dietary_flag_safe_default_when_malformed():
    for json_key, attr in _DIETARY_FLAG_KEYS.items():
        for bad_value in ("true", 1, 0, "yes", [], {}):
            data = _parse_with_single_flag(json_key, bad_value)
            assert getattr(data, attr) is False, (json_key, bad_value)


def test_dietary_flag_true_when_explicit():
    for json_key, attr in _DIETARY_FLAG_KEYS.items():
        data = _parse_with_single_flag(json_key, True)
        assert getattr(data, attr) is True, json_key


def test_dietary_flag_false_when_explicit():
    for json_key, attr in _DIETARY_FLAG_KEYS.items():
        data = _parse_with_single_flag(json_key, False)
        assert getattr(data, attr) is False, json_key


def test_malformed_numeric_field_falls_back_to_default_without_raising():
    payload = {
        "productName": "Test",
        "rawIngredientText": "Water",
        "sugarGrams": "not-a-number",
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.sugar_grams == 0.0


# --- nutrition_fields_present (used only by the barcode-enrichment path) ---
#
# Review finding 2: `nutrition_fields_present` must not accept a value
# merely because `float(value)` would succeed on it -- booleans, NaN/
# Infinity, negative values, out-of-range values, and non-numeric JSON
# types (strings, placeholders) must all be rejected; a genuinely
# explicit `0` must still be accepted.

_COMPLETE_VALID_NUTRITION = {"sugarGrams": 12.5, "sodiumMg": 200.0, "saturatedFatGrams": 1.5}


def _with_field(base: dict, key: str, value) -> str:
    payload = dict(base)
    payload[key] = value
    return json.dumps(payload)


def test_nutrition_fields_present_true_for_valid_complete_nutrition():
    assert nutrition_fields_present(json.dumps(_COMPLETE_VALID_NUTRITION)) is True


def test_nutrition_fields_present_true_for_zero_but_explicit_values():
    payload = {"sugarGrams": 0.0, "sodiumMg": 0, "saturatedFatGrams": 0.0}
    assert nutrition_fields_present(json.dumps(payload)) is True


def test_nutrition_fields_present_false_when_any_missing():
    payload = {"sugarGrams": 0.0, "sodiumMg": 15.0}  # saturatedFatGrams missing
    assert nutrition_fields_present(json.dumps(payload)) is False


def test_nutrition_fields_present_false_when_any_explicitly_null():
    for key in _COMPLETE_VALID_NUTRITION:
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, None)) is False


def test_nutrition_fields_present_false_for_boolean_values():
    """`bool` is an `int` subclass in Python -- `float(True) == 1.0`
    must NOT make a boolean pass as a trustworthy nutrition number."""
    for key in _COMPLETE_VALID_NUTRITION:
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, True)) is False
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, False)) is False


def test_nutrition_fields_present_false_for_negative_values():
    for key in _COMPLETE_VALID_NUTRITION:
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, -1.0)) is False


def test_nutrition_fields_present_false_for_nan():
    """Python's `json` module accepts the non-standard `NaN` token by
    default and hands back a real `float('nan')` -- must be caught by
    `math.isnan`, not merely by whether `float()` raised."""
    for key in _COMPLETE_VALID_NUTRITION:
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, float("nan"))) is False


def test_nutrition_fields_present_false_for_infinity():
    for key in _COMPLETE_VALID_NUTRITION:
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, float("inf"))) is False
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, key, float("-inf"))) is False


def test_nutrition_fields_present_false_for_out_of_defensible_range():
    assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, "sugarGrams", 1000.0)) is False
    assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, "saturatedFatGrams", 500.0)) is False
    assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, "sodiumMg", 5_000_000.0)) is False


def test_nutrition_fields_present_false_for_numeric_strings_and_placeholders():
    for bad_value in ("12.5", "null", "unknown", "N/A", ""):
        assert nutrition_fields_present(_with_field(_COMPLETE_VALID_NUTRITION, "sugarGrams", bad_value)) is False


def test_nutrition_fields_present_false_for_partial_nutrition():
    for key in _COMPLETE_VALID_NUTRITION:
        payload = dict(_COMPLETE_VALID_NUTRITION)
        del payload[key]
        assert nutrition_fields_present(json.dumps(payload)) is False


def test_nutrition_fields_present_false_for_invalid_json():
    assert nutrition_fields_present("not valid json {{{") is False


def test_nutrition_fields_present_false_for_non_object_json():
    assert nutrition_fields_present("[1, 2, 3]") is False
