import json

from app.services.gemini_image_parser import (
    label_field_validity,
    nutrition_fields_present,
    parse_gemini_image_json_result,
)


def test_valid_json_uses_gemini_nutrition_and_flags_directly():
    payload = {
        "productName": "Diet Cola",
        "brand": "Acme Beverages",
        "sugarGrams": 0.0,
        "sodiumMg": 15.0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100_ML",
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
    assert data.nutrition_basis == "PER_100_ML"
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
    # Safe-unknown sentinel (review finding 3): a missing novaGroup must
    # never default to a plausible-looking "3" (fabricating a specific
    # processing-level claim) -- 0 is the same "unclassified" sentinel
    # barcode_discovery.py uses, and health_score.py's else-branch
    # treats it identically to any other non-2/3/4 value (zero
    # deduction) -- see test_gemini_nova_group_validation_* below.
    assert data.nova_group == 0
    assert data.sugar_grams == 0.0
    assert data.has_artificial_sweeteners is False
    # Safe-default rule (review finding 1): an unknown dietary flag must
    # default to False, never True -- see the dedicated
    # test_dietary_flag_safe_defaults_* tests below for full coverage.
    assert data.is_vegan is False
    # Safe-unknown rule (review finding 3): absent allergen data must
    # never be represented as a confirmed "no allergens" claim -- see
    # test_gemini_allergens_* below.
    assert data.allergens_detected == ""


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

_COMPLETE_VALID_NUTRITION = {
    "sugarGrams": 12.5,
    "sodiumMg": 200.0,
    "saturatedFatGrams": 1.5,
    "nutritionBasis": "PER_100_G",
}


def _with_field(base: dict, key: str, value) -> str:
    payload = dict(base)
    payload[key] = value
    return json.dumps(payload)


def test_nutrition_fields_present_true_for_valid_complete_nutrition():
    assert nutrition_fields_present(json.dumps(_COMPLETE_VALID_NUTRITION)) is True


def test_nutrition_fields_present_true_for_zero_but_explicit_values():
    payload = {
        "sugarGrams": 0.0,
        "sodiumMg": 0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100_G",
    }
    assert nutrition_fields_present(json.dumps(payload)) is True


def test_nutrition_basis_accepts_per_100g_and_per_100ml_only_for_scoring():
    for basis in ("PER_100_G", "PER_100_ML"):
        payload = {**_COMPLETE_VALID_NUTRITION, "nutritionBasis": basis}
        assert nutrition_fields_present(json.dumps(payload)) is True

    for basis in ("PER_SERVING", "UNKNOWN", None, "per package"):
        payload = {**_COMPLETE_VALID_NUTRITION, "nutritionBasis": basis}
        assert nutrition_fields_present(json.dumps(payload)) is False


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


# --- label_field_validity / per-field granularity (review finding 1) ------


def test_label_field_validity_all_valid_when_complete_and_correct():
    payload = {**_COMPLETE_VALID_NUTRITION, "novaGroup": 4}
    validity = label_field_validity(json.dumps(payload))
    assert validity.sugar_valid is True
    assert validity.sodium_valid is True
    assert validity.saturated_fat_valid is True
    assert validity.nova_valid is True
    assert validity.all_valid is True


def test_label_field_validity_is_per_field_independent():
    """One invalid field must not mark the OTHERS invalid too."""
    payload = {"sugarGrams": 12.5, "sodiumMg": float("nan"), "saturatedFatGrams": 1.5, "novaGroup": True}
    validity = label_field_validity(json.dumps(payload))
    assert validity.sugar_valid is True
    assert validity.sodium_valid is False
    assert validity.saturated_fat_valid is True
    assert validity.nova_valid is False
    assert validity.all_valid is False  # gate still requires ALL THREE nutrition fields


# --- Review finding 1: rejected nutrition is NEVER persisted, not merely --
# --- flagged unverified -- the actual stored value must always be safe ----


def test_parse_rejects_boolean_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "sugarGrams": True}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sugar_grams == 0.0  # never 1.0 (float(True))


def test_parse_rejects_nan_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "sodiumMg": float("nan")}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sodium_mg == 0.0
    assert data.sodium_mg == data.sodium_mg  # not NaN (NaN != NaN)


def test_parse_rejects_infinity_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "saturatedFatGrams": float("inf")}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.saturated_fat_grams == 0.0


def test_parse_rejects_negative_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "sugarGrams": -50.0}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sugar_grams == 0.0


def test_parse_rejects_out_of_range_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "sugarGrams": 5000.0}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sugar_grams == 0.0


def test_parse_rejects_placeholder_string_nutrition_value_stores_safe_zero():
    payload = {"productName": "Test", "rawIngredientText": "Water", "sodiumMg": "null"}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sodium_mg == 0.0


def test_parse_preserves_individually_valid_field_alongside_a_rejected_one():
    payload = {
        "productName": "Test",
        "rawIngredientText": "Water",
        "sugarGrams": 12.5,  # valid -- must be preserved
        "sodiumMg": float("nan"),  # invalid -- must become safe 0.0
        "saturatedFatGrams": 1.5,  # valid -- must be preserved
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.sugar_grams == 12.5
    assert data.sodium_mg == 0.0
    assert data.saturated_fat_grams == 1.5


# --- Review finding 3: NOVA group strict validation -------------------------


def test_nova_group_valid_values_pass_through_unchanged():
    for value in (1, 2, 3, 4):
        payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": value}
        result = parse_gemini_image_json_result(json.dumps(payload), [])
        data, _ = result
        assert data.nova_group == value


def test_nova_group_zero_is_rejected_to_safe_unknown_sentinel():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": 0}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0  # same value, but as the UNKNOWN sentinel, not an accepted "0"


def test_nova_group_negative_is_rejected():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": -1}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


def test_nova_group_above_four_is_rejected():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": 5}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


def test_nova_group_boolean_is_rejected():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": True}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


def test_nova_group_string_is_rejected():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": "4"}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


def test_nova_group_float_is_rejected():
    payload = {"productName": "Test", "rawIngredientText": "Water", "novaGroup": 4.0}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


def test_nova_group_missing_defaults_to_safe_unknown_sentinel():
    payload = {"productName": "Test", "rawIngredientText": "Water"}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.nova_group == 0


# --- Review finding 3: allergens -- unknown must never read as "None" -----


def test_allergens_missing_key_is_unknown_empty_string():
    payload = {"productName": "Test", "rawIngredientText": "Water"}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == ""
    assert data.allergens_detected != "None"


def test_allergens_null_is_unknown_empty_string():
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": None}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == ""


def test_allergens_empty_array_is_unknown_empty_string():
    """An explicit empty array is treated the same as "not stated" --
    Gemini is not a certified allergen scanner, so an empty answer is
    not trusted as a confirmed "no allergens" claim either."""
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": []}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == ""


def test_allergens_malformed_type_is_unknown_empty_string():
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": "Milk"}  # not a list
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == ""


def test_allergens_explicit_list_is_persisted():
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": ["Milk", "Soy"]}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == "Milk, Soy"


def test_allergens_deduplicated_case_insensitively():
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": ["Milk", "milk", "Soy"]}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == "Milk, Soy"


def test_allergens_non_string_items_are_filtered_out():
    payload = {"productName": "Test", "rawIngredientText": "Water", "allergens": ["Milk", 123, None, "Soy"]}
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    data, _ = result
    assert data.allergens_detected == "Milk, Soy"
