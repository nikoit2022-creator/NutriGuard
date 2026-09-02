"""
Pure unit tests (no DB, no HTTP) for
`food_analysis._to_analyzed_data_from_discovery` — the function that
turns a validated `DiscoveredProduct` into the same shape the OCR/
Gemini pipelines use. Covers the two PR #7 review findings that live
here: unknown dietary flags must never default to a positive claim
(finding 1), and materially incomplete nutrition/ingredients must be
flagged (`nutrition_known`/`ingredients_known`), never silently
zero-filled and scored (finding 2) — plus the PR #9 review round 4
finding that nutrition and ingredients are two INDEPENDENT evidence
groups, not one combined `is_complete` flag (V11).
"""
from app.services import food_analysis
from app.integrations.barcode_providers.base import NutritionFacts
from app.services.barcode_discovery import DiscoveredProduct
from app.services.barcode_validation import validate_and_normalize

BARCODE = validate_and_normalize("4006381333931")
assert BARCODE is not None


def _discovered(**overrides) -> DiscoveredProduct:
    defaults = dict(
        barcode=BARCODE,
        product_name="Test Product",
        brand="Test Brand",
        category="Snacks",
        image_url=None,
        raw_ingredient_text="",
        nutrition=NutritionFacts(),
        allergens=[],
        dietary_flags={},
        language="en",
        primary_provider="open_food_facts",
        confidence=0.75,
        source_url=None,
        external_last_modified=None,
        nutrition_known=False,
        ingredients_known=False,
        is_conflicting=False,
        contributing=[],
    )
    defaults.update(overrides)
    return DiscoveredProduct(**defaults)


# --- Finding 1: unknown dietary flags must not become positive claims -------


def test_unknown_dietary_flags_default_to_false_not_true():
    discovered = _discovered(
        raw_ingredient_text="sugar, water, salt",
        nutrition=NutritionFacts(sugar_grams=1.0, sodium_mg=1.0, saturated_fat_grams=1.0),
        nutrition_known=True,
        ingredients_known=True,
        dietary_flags={},  # provider stated nothing
    )
    data, _ingredients, nutrition_known, ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert nutrition_known is True
    assert ingredients_known is True
    assert data.is_vegan is False
    assert data.is_vegetarian is False
    assert data.is_gluten_free is False
    assert data.is_lactose_free is False
    assert data.is_halal is False
    assert data.is_kosher is False


def test_explicit_dietary_flags_from_provider_are_honored():
    discovered = _discovered(
        raw_ingredient_text="sugar, water",
        nutrition=NutritionFacts(sugar_grams=1.0, sodium_mg=1.0, saturated_fat_grams=1.0),
        nutrition_known=True,
        ingredients_known=True,
        dietary_flags={"is_vegan": True, "is_gluten_free": True, "is_halal": False},
    )
    data, _ingredients, _nutrition_known, _ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert data.is_vegan is True
    assert data.is_gluten_free is True
    assert data.is_halal is False  # explicit False is honored too, not overridden
    # still not claimed, since the provider never stated it:
    assert data.is_kosher is False


# --- Finding 2: materially incomplete data must not be scored ---------------


def test_identity_only_discovery_is_flagged_incomplete():
    """UPCitemdb-shaped result: has a name/brand, no nutrition, no
    ingredients at all."""
    discovered = _discovered(nutrition_known=False, ingredients_known=False)
    data, ingredients, nutrition_known, ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert nutrition_known is False
    assert ingredients_known is False
    assert ingredients == []
    assert data.nova_group == 0  # explicit "unclassified" sentinel, not a guess


def test_nutrition_known_but_ingredients_unknown_is_still_incomplete():
    """V11 (PR #9 review round 4): nutrition and ingredients are tracked
    INDEPENDENTLY -- nutrition_known is True on its own here, but the
    combined completeness (`nutrition_known and ingredients_known`,
    what `_persist_discovered_product` uses for `is_verified`) is still
    False. A later barcode+label-image scan can independently complete
    the ingredients group without needing to resupply nutrition -- see
    tests/integration/test_label_barcode_enrichment.py's cumulative-
    completeness tests."""
    discovered = _discovered(
        nutrition=NutritionFacts(sugar_grams=5.0, sodium_mg=100.0, saturated_fat_grams=1.0),
        nutrition_known=True,
        ingredients_known=False,
        raw_ingredient_text="",
    )
    data, _ingredients, nutrition_known, ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert nutrition_known is True
    assert ingredients_known is False
    assert (nutrition_known and ingredients_known) is False
    assert data.sugar_grams == 5.0  # the genuinely-known group is still used, not discarded


def test_ingredients_known_but_nutrition_unknown_is_still_incomplete():
    discovered = _discovered(
        nutrition=NutritionFacts(),
        nutrition_known=False,
        ingredients_known=True,
        raw_ingredient_text="sugar, water, salt",
    )
    _data, ingredients, nutrition_known, ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert nutrition_known is False
    assert ingredients_known is True
    assert (nutrition_known and ingredients_known) is False
    assert len(ingredients) == 3  # the genuinely-known group is still used, not discarded


def test_complete_discovery_is_not_flagged_incomplete_and_uses_real_nutrition():
    discovered = _discovered(
        nutrition=NutritionFacts(sugar_grams=12.5, sodium_mg=340.0, saturated_fat_grams=2.0, nova_group=3),
        nutrition_known=True,
        ingredients_known=True,
        raw_ingredient_text="sugar, water, salt",
    )
    data, _ingredients, nutrition_known, ingredients_known = food_analysis._to_analyzed_data_from_discovery(
        discovered, []
    )
    assert nutrition_known is True
    assert ingredients_known is True
    assert data.sugar_grams == 12.5
    assert data.sodium_mg == 340.0
    assert data.nova_group == 3


def test_label_scan_required_details_shape():
    from app.models.product import Product

    product = Product(
        barcode="4006381333931",
        product_name="Some Product",
        brand="Some Brand",
        category="cat",
        image_url="https://example.com/x.jpg",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        has_verified_nutrition=False,
    )
    details = food_analysis._label_scan_required_details(product)
    assert details["labelScanRequired"] is True
    assert details["discoveredIdentity"]["productName"] == "Some Product"
    assert details["discoveredIdentity"]["barcode"] == "4006381333931"
    assert "suggestedAction" in details
