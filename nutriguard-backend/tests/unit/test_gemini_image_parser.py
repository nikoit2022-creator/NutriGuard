import json

from app.services.gemini_image_parser import parse_gemini_image_json_result


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
    assert data.is_vegan is True


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


# -- bilingual output requirements -------------------------------------------

def test_english_label_names_pass_through_unchanged():
    payload = {
        "productName": "Diet Cola",
        "brand": "Acme Beverages",
        "rawIngredientText": "Carbonated Water, Aspartame (E951)",
        "ingredients": [{"commonName": "Aspartame", "eNumber": "E951"}],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.product_name == "Diet Cola"
    assert data.brand == "Acme Beverages"


def test_bulgarian_label_names_are_preserved_not_mangled():
    payload = {
        "productName": "Диетична Кола",
        "brand": "Acme Beverages",
        "rawIngredientText": "Газирана вода, Аспартам",
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.product_name == "Диетична Кола"


def test_third_language_product_name_is_translated_not_leaked():
    payload = {
        "productName": "Zucker Drink",
        "rawIngredientText": "Wasser, Zucker",
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.product_name == "Sugar Drink"
    assert "Zucker" not in data.product_name


def test_third_language_unmatched_ingredient_name_is_not_leaked_untranslated():
    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Sheqer, Uje",
        "ingredients": [{"commonName": "Sheqer", "eNumber": None}],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    _, ingredients = result
    assert len(ingredients) == 1
    assert ingredients[0].common_name == "Sugar"
    assert "Sheqer" not in ingredients[0].common_name


def test_null_and_none_string_product_name_triggers_full_fallback():
    """A literal "null"/"None" placeholder for a REQUIRED field is exactly
    as unusable as a genuinely missing field -- it must trigger the same
    None-return (full deterministic fallback) as a missing productName,
    not persist "null" as the product name."""
    payload = {"productName": "null", "rawIngredientText": "Water"}
    assert parse_gemini_image_json_result(json.dumps(payload), []) is None

    payload_none = {"productName": "None", "rawIngredientText": "Water"}
    assert parse_gemini_image_json_result(json.dumps(payload_none), []) is None


def test_null_string_raw_ingredient_text_triggers_full_fallback():
    payload = {"productName": "Test Product", "rawIngredientText": "null"}
    assert parse_gemini_image_json_result(json.dumps(payload), []) is None


def test_null_string_brand_uses_the_optional_field_fallback_not_the_literal_text():
    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Water",
        "brand": "null",
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.brand == "Analyzed Brand"


def test_brand_name_is_never_translated_even_if_it_matches_a_glossary_word():
    payload = {
        "productName": "Test Product",
        "rawIngredientText": "Water",
        "brand": "Zucker GmbH",  # a real brand name that happens to contain a glossary word
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [])
    assert result is not None
    data, _ = result
    assert data.brand == "Zucker GmbH"


def test_bulgarian_ingredient_matches_scientific_database_via_english_alias():
    """API Contract requirement: a Bulgarian ingredient name is matched
    against the scientific database via its English canonical name/
    E-number, exactly like the English-language path already tested
    above (`test_gemini_ingredient_matches_scientific_database_when_present`)."""

    class FakeDbIngredient:
        id = "e951_aspartame"
        common_name = "Aspartame"
        scientific_name = "L-alpha-aspartyl..."
        e_number = "E951"

    payload = {
        "productName": "Тестов продукт",
        "rawIngredientText": "Аспартам (E951)",
        "ingredients": [{"commonName": "Аспартам", "eNumber": "E951"}],
    }
    result = parse_gemini_image_json_result(json.dumps(payload), [FakeDbIngredient()])
    assert result is not None
    _, ingredients = result
    assert len(ingredients) == 1
    assert ingredients[0].id == "e951_aspartame"  # matched, not a synthetic fallback
