from types import SimpleNamespace

from app.schemas.product import ProductOut
from app.services.barcode_text_safety import clean_optional
from app.services.ocr_normalizer import normalize_and_extract_tokens
from app.services.product_label import dietary_statuses, split_ingredients_and_traces, stated_allergens


LABEL = (
    "Wheat flour, sugar, vegetable fat (non-hydrogenated palm oil), "
    "emulsifier soya lecithin, skim milk powder, ground peanut kernels, "
    "ground hazelnut kernels, egg melange. May contain sesame seeds."
)


def test_trace_statement_is_not_an_ingredient_or_confirmed_allergen():
    ingredients, traces = split_ingredients_and_traces(LABEL)
    assert traces == "sesame seeds"
    assert ingredients.endswith("egg melange")
    assert len(normalize_and_extract_tokens(LABEL)) == 8
    assert stated_allergens(LABEL) == {"Gluten", "Milk", "Peanuts", "Nuts", "Eggs", "Soybeans"}


def test_no_trace_marker_does_not_remove_ordinary_ingredients():
    assert split_ingredients_and_traces("Sesame seeds, salt") == ("Sesame seeds, salt", "")
    assert "Sesame" in stated_allergens("Sesame seeds, salt")


def test_absent_dietary_evidence_is_unknown_not_negative():
    product = SimpleNamespace(
        raw_ingredient_text=LABEL, is_gluten_free=False, is_lactose_free=False,
        is_vegan=False, is_vegetarian=False, is_halal=False, is_kosher=False,
    )
    assert dietary_statuses(product) == {
        "glutenFree": "no", "lactoseFree": "no", "vegan": "no",
        "vegetarian": "unknown", "halal": "unknown", "kosher": "unknown",
    }


def test_legacy_positive_defaults_are_not_certification():
    local = SimpleNamespace(
        source="label_scan", raw_ingredient_text="Water, sugar",
        is_gluten_free=True, is_lactose_free=True, is_vegan=True,
        is_vegetarian=True, is_halal=True, is_kosher=True,
    )
    assert set(dietary_statuses(local).values()) == {"unknown"}
    local.source = "open_food_facts"
    assert set(dietary_statuses(local).values()) == {"yes"}


def test_explicit_provider_negative_is_preserved():
    product = SimpleNamespace(
        source="open_food_facts", raw_ingredient_text="Water, sugar",
        dietary_evidence_json='{"is_vegetarian": false}',
        is_gluten_free=False, is_lactose_free=False, is_vegan=False,
        is_vegetarian=False, is_halal=False, is_kosher=False,
    )
    assert dietary_statuses(product)["vegetarian"] == "no"


def test_provider_entities_are_decoded_and_legacy_rows_display_cleanly():
    assert clean_optional("Sugar biscuits &quot;For Coffee&quot;") == 'Sugar biscuits "For Coffee"'
    data = dict(
        barcode="4823077614675", product_name="Sugar biscuits &quot;For Coffee&quot;",
        brand="Roshen", category="Biscuit", raw_ingredient_text=LABEL,
        ingredient_ids="", health_score=38, nova_group=4, sugar_grams=20.7,
        sodium_mg=284, saturated_fat_grams=8, has_artificial_sweeteners=False,
        has_preservatives=False, is_gluten_free=False, is_lactose_free=False,
        is_vegan=False, is_vegetarian=False, is_halal=False, is_kosher=False,
        allergens_detected="Gluten, Milk", timestamp=1,
        has_verified_nutrition=True, has_verified_ingredients=True, is_verified=True,
    )
    out = ProductOut(**data).model_dump(by_alias=True)
    assert out["productName"] == 'Sugar biscuits "For Coffee"'
    assert out["possibleTraces"] == "sesame seeds"
    assert "Peanuts" in out["allergensDetected"]
    assert "Eggs" in out["allergensDetected"]
    assert "Sesame" not in out["allergensDetected"]
    assert out["dietaryStatuses"]["halal"] == "unknown"
