"""
`scripts.audit_catalogue_readonly._audit` on a small synthetic catalogue:
classification of unresolved product references (recoverable legacy vs
ambiguous vs genuinely missing), E-number case collisions, priority
ingredient reporting, the completeness section, and that the audit issues
only SELECT statements.
"""
import uuid

import pytest
from sqlalchemy import event

from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.product import Product
from app.services.ingredient_normalization import normalize_ingredient_name
from scripts.audit_catalogue_readonly import _audit

CYR_E = "Е"


def _ing(**kw) -> Ingredient:
    base = dict(
        common_name="x", normalized_name="x", scientific_name="", category="", description="",
        purpose_in_food="", health_concerns="", evidence_level="", countries_restricted_or_banned="",
        efsa_status="", fda_status="", acceptable_daily_intake="", side_effects="", allergens="",
        references="", risk_level=RiskLevel.SAFE, risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )
    base.update(kw)
    return Ingredient(**base)


def _product(barcode: str, raw: str, ids: str) -> Product:
    return Product(
        barcode=barcode, product_name="p", brand="", category="", raw_ingredient_text=raw, ingredient_ids=ids,
        health_score=0, nova_group=0, sugar_grams=0, sodium_mg=0, saturated_fat_grams=0, allergens_detected="",
        source="label_scan", is_verified=False, has_verified_nutrition=False, has_verified_ingredients=True,
    )


@pytest.mark.asyncio
async def test_audit_classifies_references_and_reports_collisions_read_only(db_session):
    db_session.add_all(
        [
            _ing(id="e300_ascorbic_acid", common_name="Ascorbic acid", normalized_name="ascorbic acid", e_number="E300",
                 source=IngredientSource.CURATED_SEED, verification_status=IngredientVerificationStatus.LIMITED_DATA,
                 purpose_in_food="Antioxidant"),
            _ing(id="e150d_curated", common_name="Sulphite ammonia caramel", e_number="E150d",
                 source=IngredientSource.CURATED_SEED, verification_status=IngredientVerificationStatus.LIMITED_DATA),
            _ing(id="synth_e150d_ocr", common_name="Colorant: e150d", e_number="E150D"),
            _ing(id="synth_water_6d5a45920a15", common_name="Water", normalized_name="water"),
        ]
    )
    await db_session.flush()
    db_session.add(
        IngredientAlias(
            id=uuid.uuid4(), ingredient_id="synth_water_6d5a45920a15", alias_text="Water",
            alias_normalized=normalize_ingredient_name("Water"), language=None, source=IngredientSource.OCR_HEURISTIC,
        )
    )
    db_session.add_all(
        [
            # generation-0 bare slug, supported by stored text
            _product("9100000000001", "cow's milk, live sourdough", "synth_cows_milk"),
            # generation-0 id shared by several Cyrillic tokens -> ambiguous
            _product("9100000000002", "вода, сол", "synth_"),
            # no supporting token at all -> genuinely missing (but valid synthetic shape)
            _product("9100000000003", "sugar", "synth_unrelated_thing"),
            # id that is not a synthetic shape and not in the catalogue -> dangling/corrupt
            _product("9100000000004", "salt", "zzz_missing_row"),
            # legacy Cyrillic-E token whose catalogue E-number row exists
            _product("9100000000005", f"{CYR_E}300", "synth_300"),
            _product("9100000000006", "water", "synth_water_6d5a45920a15,e300_ascorbic_acid"),
        ]
    )
    await db_session.commit()

    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def _spy(conn, cursor, statement, params, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", _spy)
    try:
        report = await _audit(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _spy)
    assert statements and set(statements) == {"SELECT"}

    by_class = report["unresolved_references"]["by_class"]
    assert by_class["RECOVERABLE_LEGACY_BARE_SLUG_GEN0"]["references"] == 2  # cows_milk + synth_300
    assert by_class["AMBIGUOUS"]["references"] == 1
    assert by_class["MISSING_SYNTHETIC_READABLE_SLUG_ONLY"]["references"] == 1
    assert by_class["MISSING_NON_SYNTHETIC_ID_DANGLING_OR_CORRUPT"]["references"] == 1
    assert report["unresolved_references"]["total_references"] == 5

    collisions = report["e_number_uniqueness"]["case_collisions_by_e_number"]
    assert {r["id"] for r in collisions["E150D"]} == {"e150d_curated", "synth_e150d_ocr"}

    e300 = next(p for p in report["priority_ingredients"] if p["target"] == "E300")
    assert [r["id"] for r in e300["rows"]] == ["e300_ascorbic_acid"]
    assert e300["synthetic_references_not_reaching_a_row"] == {"synth_300": 1}
    assert e300["rows"][0]["empty_fields"]  # non-empty purpose does not hide the other gaps

    reached = report["completeness"]["references_reaching_no_row_but_matching_a_catalogue_e_number"]
    assert reached["count"] == 1 and reached["rows"][0]["catalogue_row"] == "e300_ascorbic_acid"
    assert "NOT_YET_RESEARCHED" in report["completeness"]["state_counts_among_referenced_ingredients"]
