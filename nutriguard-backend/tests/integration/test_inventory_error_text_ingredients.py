"""
Dry-run inventory of historical error-text ingredient rows
(`scripts.inventory_error_text_ingredients`): finds only the known
provider-failure sentences, lists their aliases/products/candidates, never
touches ordinary ingredients, and issues SELECT statements only.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_candidate import IngredientCandidate
from app.models.product import Product
from app.services.ingredient_normalization import normalize_ingredient_name
from scripts.inventory_error_text_ingredients import _inventory

ERR_ID = "synth_ingredients_could_not_be_extracted_from_the_i_a379a1a35617"
ERR2_ID = "synth_ai_response_was_unavailable_or_invalid_8f86bdc33890"


def _ing(i: str, name: str) -> Ingredient:
    return Ingredient(
        id=i, common_name=name, normalized_name=normalize_ingredient_name(name), scientific_name="", category="",
        description="", purpose_in_food="", health_concerns="", evidence_level="",
        countries_restricted_or_banned="", efsa_status="", fda_status="", acceptable_daily_intake="",
        side_effects="", allergens="", references="", risk_level=RiskLevel.SAFE, risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )


def _product(barcode: str, ids: str) -> Product:
    return Product(
        barcode=barcode, product_name="p", brand="", category="", raw_ingredient_text="", ingredient_ids=ids,
        health_score=0, nova_group=0, sugar_grams=0, sodium_mg=0, saturated_fat_grams=0, allergens_detected="",
        source="label_scan", is_verified=False, has_verified_nutrition=False,
    )


@pytest.mark.asyncio
async def test_inventory_lists_error_text_rows_and_references_read_only(db_session):
    db_session.add_all(
        [
            _ing(ERR_ID, "Ingredients could not be extracted from the image"),
            _ing(ERR2_ID, "AI response was unavailable or invalid"),
            _ing("synth_water_6d5a45920a15", "Water"),
        ]
    )
    await db_session.flush()
    db_session.add_all(
        [
            IngredientAlias(
                id=uuid.uuid4(), ingredient_id=ERR_ID, alias_text="Ingredients could not be extracted from the image",
                alias_normalized="ingredients could not be extracted from the image", language=None,
                source=IngredientSource.OCR_HEURISTIC,
            ),
            IngredientCandidate(
                normalized_key="ai response was unavailable or invalid", display_name="AI response was unavailable or invalid",
                ingredient_id=ERR2_ID, status="PENDING", flags="PLACEHOLDER_TEXT", encounter_count=3,
                first_seen_at=datetime.now(timezone.utc), last_seen_at=datetime.now(timezone.utc),
            ),
            _product("9300000000001", f"{ERR_ID},{ERR2_ID},synth_water_6d5a45920a15"),
            _product("9300000000002", ERR_ID),
            _product("9300000000003", "synth_water_6d5a45920a15"),
        ]
    )
    await db_session.commit()

    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def _spy(conn, cursor, statement, params, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", _spy)
    try:
        report = await _inventory(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _spy)
    assert statements and set(statements) == {"SELECT"}

    assert report["dry_run"] is True
    assert report["error_text_rows"] == 2
    assert report["distinct_products_referencing"] == 2
    assert report["total_product_references"] == 3
    rows = {r["id"]: r for r in report["rows"]}
    assert set(rows) == {ERR_ID, ERR2_ID}  # the ordinary "Water" row is never listed
    assert [r["barcode"] for r in rows[ERR_ID]["product_references"]] == ["9300000000001", "9300000000002"]
    assert rows[ERR_ID]["aliases"] == ["Ingredients could not be extracted from the image"]
    assert rows[ERR2_ID]["candidate_queue_rows"][0]["encounter_count"] == 3
    assert report["oldest_referencing_product_created_at"] is not None
