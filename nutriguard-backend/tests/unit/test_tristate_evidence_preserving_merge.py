"""
Issue #21 follow-up: an UNKNOWN incoming value must never overwrite
supported existing evidence, on every path that overwrites an existing
`Product` row's dietary flags / allergens
(`food_analysis._apply_dietary_flags`, `_apply_discovered_fields`,
`_apply_label_enrichment`) -- plus the `Product` model defaults
(audit "Finding 5": the ORM used to default every flag to True).
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.product import Product
from app.services import dietary_suitability as ds
from app.services import food_analysis
from app.services.fallback_analysis import AnalyzedProductData
from app.services.gemini_image_parser import LabelFieldValidity


def _data(allergens: str = "", ingredients_text: str = "water", **flags) -> AnalyzedProductData:
    values = {name: None for name in ds.FLAG_NAMES}
    values.update(flags)
    return AnalyzedProductData(
        barcode="4006381333931",
        product_name="Test Product",
        brand="Test Brand",
        category="Snacks",
        image_url=None,
        raw_ingredient_text=ingredients_text,
        nova_group=3,
        sugar_grams=1.0,
        sodium_mg=1.0,
        saturated_fat_grams=1.0,
        has_artificial_sweeteners=False,
        has_preservatives=False,
        allergens_detected=allergens,
        **values,
    )


def _existing(**overrides) -> Product:
    fields = dict(
        barcode="4006381333931",
        product_name="Real Name",
        brand="Real Brand",
        category="Snacks",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=None,
        nova_group=3,
        is_gluten_free=True,
        is_lactose_free=False,
        is_vegan=True,
        is_vegetarian=None,
        is_halal=None,
        is_kosher=None,
        allergens_detected="Milk",
        source="open_food_facts",
        is_verified=False,
        has_verified_nutrition=False,
        has_verified_ingredients=False,
    )
    fields.update(overrides)
    return Product(**fields)


# --- the shared helper --------------------------------------------------------


def test_unknown_incoming_flags_never_overwrite_supported_existing_values():
    existing = _existing()
    food_analysis._apply_dietary_flags(existing, _data())  # every incoming flag is unknown
    assert existing.is_gluten_free is True  # supported True kept
    assert existing.is_lactose_free is False  # supported False kept
    assert existing.is_vegan is True
    assert existing.is_vegetarian is None  # still unknown (nothing to keep)


def test_explicit_incoming_values_do_replace_existing_ones():
    existing = _existing()
    food_analysis._apply_dietary_flags(existing, _data(is_gluten_free=False, is_lactose_free=True, is_halal=True))
    assert existing.is_gluten_free is False  # newer supported evidence wins
    assert existing.is_lactose_free is True
    assert existing.is_halal is True
    assert existing.is_vegan is True  # untouched (incoming unknown)


# --- discovery overwrite path ---------------------------------------------------


def test_rediscovery_overwrite_preserves_existing_flags_and_allergens_against_unknown():
    existing = _existing()
    food_analysis._apply_discovered_fields(
        existing,
        _data(allergens=""),
        [],
        source="open_food_facts",
        confidence=0.9,
        has_verified_nutrition=False,
        has_verified_ingredients=False,
    )
    assert existing.is_gluten_free is True
    assert existing.is_vegan is True
    assert existing.allergens_detected == "Milk"  # "" (unknown) never erases a known list


def test_rediscovery_overwrite_applies_explicit_values_and_known_allergens():
    existing = _existing()
    food_analysis._apply_discovered_fields(
        existing,
        _data(allergens="Soy", is_vegan=False),
        [],
        source="open_food_facts",
        confidence=0.9,
        has_verified_nutrition=False,
        has_verified_ingredients=False,
    )
    assert existing.is_vegan is False
    assert existing.allergens_detected == "Soy"


# --- label enrichment path ------------------------------------------------------


def _enrich(existing: Product, data: AnalyzedProductData, *, trustworthy: bool = True) -> None:
    food_analysis._apply_label_enrichment(existing, data, [], LabelFieldValidity(), trustworthy, "label_scan")


def test_label_enrichment_with_unknown_flags_preserves_supported_evidence():
    existing = _existing()
    # A complete ingredients group refreshes the row, but its flags are all unknown
    # (e.g. a Bulgarian label the heuristic cannot read):
    _enrich(existing, _data(ingredients_text="Пшенично брашно, мляко"))
    assert existing.has_verified_ingredients is True  # the group DID refresh...
    assert existing.raw_ingredient_text == "Пшенично брашно, мляко"
    assert existing.is_gluten_free is True  # ...but unknown did not erase the supported flags
    assert existing.is_lactose_free is False
    assert existing.is_vegan is True
    assert existing.allergens_detected == "Milk"


def test_label_enrichment_with_explicit_evidence_replaces_flags():
    existing = _existing()
    _enrich(existing, _data(ingredients_text="wheat flour", is_gluten_free=False))
    assert existing.is_gluten_free is False


def test_label_enrichment_unknown_never_turns_into_true_on_a_fresh_row():
    existing = _existing(
        is_gluten_free=None, is_lactose_free=None, is_vegan=None, allergens_detected="", source="local"
    )
    _enrich(existing, _data(ingredients_text="Пшенично брашно"))
    assert existing.is_gluten_free is None
    assert existing.is_lactose_free is None
    assert existing.is_vegan is None
    assert existing.allergens_detected == ""


# --- model defaults (audit Finding 5) -------------------------------------------


@pytest.mark.asyncio
async def test_product_model_defaults_are_unknown_not_certified(db_engine):
    """A write path that forgets these columns must inherit UNKNOWN (NULL),
    never `True` ("certified compliant") -- and never the literal "None"."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as db:
        db.add(
            Product(
                barcode="9990000000099",
                product_name="Bare Product",
                raw_ingredient_text="",
                ingredient_ids="",
                nova_group=3,
            )
        )
        await db.commit()
        row = (await db.execute(select(Product).where(Product.barcode == "9990000000099"))).scalar_one()
        for name in ds.FLAG_NAMES:
            assert getattr(row, name) is None, name
        assert row.allergens_detected == ""
        assert row.health_score is None
        assert row.is_verified is False


def test_new_product_model_has_no_score_placeholder_and_keeps_unknown_flags():
    product = food_analysis._to_product_model("4006381333931", _data(is_vegan=True), [])
    assert product.health_score is None  # was a stored 0 placeholder
    assert product.is_vegan is True
    assert product.is_gluten_free is None
    assert product.is_verified is False
