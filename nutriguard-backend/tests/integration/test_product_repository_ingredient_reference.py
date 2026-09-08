"""
DB-backed tests for `product_repository.has_ingredient_reference` (PR
#13 review round 2: "canonical identity precedence" -- see
`ingredient_catalog._reconcile_official_identifier_conflict`, the only
caller). Code-review finding: the original implementation built its
`LIKE` patterns directly from `ingredient_id` with no wildcard
escaping -- SQL `LIKE` treats a bare `_` as "any single character" and
`%` as "any run of characters", both of which real `Ingredient.id`s
routinely contain (e.g. "e300_ascorbic_acid", "synth_..."), so an id
containing either could false-positive-match an entirely different id
that merely has the same shape. Fixed by `_escape_like`.
"""
import pytest

from app.models.product import Product
from app.repositories import product_repository


def _product(barcode: str, ingredient_ids: str) -> Product:
    return Product(
        barcode=barcode,
        product_name="Test Product",
        brand="",
        category="",
        raw_ingredient_text="",
        ingredient_ids=ingredient_ids,
        health_score=50,
        nova_group=1,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="label_scan",
    )


@pytest.mark.asyncio
async def test_underscore_in_ingredient_id_is_not_treated_as_a_sql_wildcard(db_session):
    """The exact false positive the code review reproduced: an id
    containing `_` (a real, common shape -- e.g. "e300_ascorbic_acid")
    must NOT match a product whose `ingredient_ids` merely has the same
    LENGTH/SHAPE with a different character where the `_` is -- `LIKE`
    would otherwise treat `_` as "any single character" and wrongly
    report a reference that doesn't actually exist."""
    db_session.add(_product("0000000000010", "other_id,e300Xascorbic_acid"))
    await db_session.flush()

    assert await product_repository.has_ingredient_reference(db_session, "e300_ascorbic_acid") is False


@pytest.mark.asyncio
async def test_percent_in_ingredient_id_is_not_treated_as_a_sql_wildcard(db_session):
    """Same false-positive class for `%` ("any run of characters")."""
    db_session.add(_product("0000000000011", "some_other_totally_unrelated_id"))
    await db_session.flush()

    assert await product_repository.has_ingredient_reference(db_session, "some%") is False


@pytest.mark.asyncio
async def test_a_genuine_underscore_containing_id_is_still_correctly_found(db_session):
    """The fix must not turn genuine matches into false NEGATIVES either
    -- an id with `_` that IS really present must still be found, in
    each comma-boundary position (sole entry, first, last, middle)."""
    ingredient_id = "e300_ascorbic_acid"
    for barcode, ingredient_ids in [
        ("1", ingredient_id),
        ("2", f"{ingredient_id},other_id"),
        ("3", f"other_id,{ingredient_id}"),
        ("4", f"before_id,{ingredient_id},after_id"),
    ]:
        db_session.add(_product(barcode, ingredient_ids))
    await db_session.flush()

    assert await product_repository.has_ingredient_reference(db_session, ingredient_id) is True


@pytest.mark.asyncio
async def test_a_plain_substring_is_still_correctly_rejected(db_session):
    """Pre-existing boundary-safety guarantee, unaffected by the
    escaping fix: "e300" must not match a product only referencing the
    longer id "e3001"."""
    db_session.add(_product("0000000000012", "e3001"))
    await db_session.flush()

    assert await product_repository.has_ingredient_reference(db_session, "e300") is False


@pytest.mark.asyncio
async def test_no_product_references_the_id_at_all(db_session):
    assert await product_repository.has_ingredient_reference(db_session, "never_referenced_id") is False
