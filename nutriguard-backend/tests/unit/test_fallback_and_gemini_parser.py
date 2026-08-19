from app.services.fallback_analysis import fallback_local_analysis
from app.services.gemini_result_parser import parse_gemini_json_result


def test_fallback_local_analysis_detects_sugar_and_sodium_keywords():
    product, ingredients = fallback_local_analysis(
        "Test Product", "Water, Sugar, Salt, Sodium Benzoate (E211)", []
    )
    assert product.sugar_grams == 14.0
    assert product.sodium_mg == 450.0
    assert product.has_preservatives is True  # "benzoate" keyword
    assert product.barcode.startswith("ocr_")


def test_fallback_local_analysis_low_risk_defaults_when_no_keywords():
    product, ingredients = fallback_local_analysis("Plain Water", "Water", [])
    assert product.sugar_grams == 2.0
    assert product.sodium_mg == 80.0
    assert product.has_artificial_sweeteners is False


def test_fallback_local_analysis_gluten_and_lactose_detection():
    product, _ = fallback_local_analysis("Bread", "Wheat Flour, Milk, Salt", [])
    assert product.is_gluten_free is False
    assert product.is_lactose_free is False


def test_fallback_local_analysis_creates_synthetic_ingredients_for_unknown_tokens():
    product, ingredients = fallback_local_analysis("Test", "Aspartame, Unobtainium", [])
    ids = [i.id for i in ingredients]
    assert any(i.startswith("synth_") for i in ids)


def test_gemini_result_parser_uses_only_product_name_from_json():
    """Documented quirk (API Contract / gemini_result_parser docstring):
    brand, nutrition figures, and the ingredients array from a successful
    Gemini response are parsed but discarded; only productName feeds into
    the deterministic fallback, which recomputes everything from the
    ORIGINAL raw text via keyword heuristics."""
    fake_json = (
        '{"productName": "Diet Soda", "brand": "Acme", "sugarGrams": 0.0, '
        '"sodiumMg": 5.0, "novaGroup": 4}'
    )
    original_raw_text = "Water, Sugar, Salt"  # deliberately different nutrition profile
    product, _ = parse_gemini_json_result(fake_json, original_raw_text, [])

    assert product.product_name == "Diet Soda"  # taken from Gemini JSON
    assert product.brand != "Acme"  # brand from Gemini JSON is discarded
    # sugar/sodium come from fallback_local_analysis(original_raw_text), NOT
    # from the Gemini-provided sugarGrams=0.0 / sodiumMg=5.0
    assert product.sugar_grams == 14.0
    assert product.sodium_mg == 450.0


def test_gemini_result_parser_falls_back_to_generic_name_on_malformed_json():
    product, _ = parse_gemini_json_result("not valid json at all", "Water, Salt", [])
    assert product.product_name == "Scanned Product"
