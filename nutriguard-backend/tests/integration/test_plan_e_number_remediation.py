"""
Dry-run E-number remediation plan (`scripts.plan_e_number_remediation`).

Fixture mirrors the shape of the live E150D / E150d collision observed on
2026-10-05 (OCR row with 6 product references + curated row with a BG
localization + a legacy Cyrillic-E row), built from synthetic rows only.
Proves: the plan names the canonical target and the affected references/
aliases/localizations, keeps the target's fields untouched, never selects
across subtype suffixes, and issues NO write statement.
"""
import uuid

import pytest
from sqlalchemy import event, select

from app.models.enums import (
    IngredientSource,
    IngredientTranslationSource,
    IngredientTranslationStatus,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product
from scripts.plan_e_number_remediation import _load


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


def _alias(ingredient_id: str, text: str) -> IngredientAlias:
    from app.services.ingredient_normalization import normalize_ingredient_name

    return IngredientAlias(
        id=uuid.uuid4(), ingredient_id=ingredient_id, alias_text=text,
        alias_normalized=normalize_ingredient_name(text), language=None, source=IngredientSource.OCR_HEURISTIC,
    )


def _product(barcode: str, ids: str) -> Product:
    return Product(
        barcode=barcode, product_name="p", brand="", category="", raw_ingredient_text="", ingredient_ids=ids,
        health_score=0, nova_group=0, sugar_grams=0, sodium_mg=0, saturated_fat_grams=0, allergens_detected="",
        source="label_scan", is_verified=False, has_verified_nutrition=False,
    )


CURATED = "e150d_sulphite_ammonia_caramel"
OCR = "synth_colorant_e150d_0495700507e9"
LEGACY_CYR = "synth_150d_9b5f685f52c3"


async def _seed(db) -> None:
    db.add_all(
        [
            _ing(id=CURATED, common_name="Sulphite ammonia caramel", e_number="E150d", ins_number="150d",
                 source=IngredientSource.CURATED_SEED, verification_status=IngredientVerificationStatus.LIMITED_DATA,
                 confidence=0.9, description="Colour", purpose_in_food="Colouring"),
            _ing(id=OCR, common_name="Colorant: e150d", e_number="E150D", ins_number="150D"),
            _ing(id=LEGACY_CYR, common_name="Оцветител (Е 150d", e_number=None),
            # unrelated subtype that must never be grouped with E150d
            _ing(id="e150a_plain_caramel", common_name="Plain caramel", e_number="E150a",
                 source=IngredientSource.CURATED_SEED, verification_status=IngredientVerificationStatus.LIMITED_DATA),
            # legacy Cyrillic row whose E-number has no catalogue row
            _ing(id="synth_414_x", common_name="Стабилизатор: Е414", e_number=None),
        ]
    )
    await db.flush()
    db.add_all(
        [
            _alias(OCR, "Colorant: e150d"),
            _alias(LEGACY_CYR, "Оцветител (Е 150d"),
            _alias(CURATED, "E150d"),
            IngredientLocalization(
                ingredient_id=CURATED, language="bg", common_name="Карамел", translation_status=IngredientTranslationStatus.DRAFT,
                translation_source=IngredientTranslationSource.MACHINE_TRANSLATED, source_content_hash="h",
            ),
        ]
    )
    for n in range(6):
        db.add(_product(f"90000000000{n}", f"{OCR},water"))
    db.add(_product("9000000000100", LEGACY_CYR))
    await db.commit()


@pytest.mark.asyncio
async def test_plan_names_target_references_and_leaves_target_untouched(db_session):
    await _seed(db_session)
    plan = await _load(db_session)

    assert plan["dry_run"] is True
    assert plan["remaining_case_collisions"] == 1
    (collision,) = plan["case_collisions"]
    assert collision["normalized_e_number"] == "E150D"
    assert collision["canonical_target"]["id"] == CURATED
    assert collision["canonical_target"]["localizations"] == ["bg"]
    assert collision["fields_modified_on_target"] == []
    assert collision["needs_manual_decision"] is False

    (dup,) = collision["duplicates"]
    assert dup["row"]["id"] == OCR
    assert len(dup["row"]["product_barcodes"]) == 6
    assert dup["row"]["aliases"] == ["Colorant: e150d"]
    assert dup["preserve"]["source_row_kept"] is True
    assert dup["requires_manual_review"] is False  # OCR row carries no narrative to lose

    # subtype E150a is never pulled into the E150d group
    assert all("E150A" not in c["normalized_e_number"] for c in plan["case_collisions"])

    legacy = {e["normalized_e_number"]: e for e in plan["legacy_cyrillic_e_rows"]}
    assert legacy["E150D"]["canonical_target"]["id"] == CURATED
    assert legacy["E150D"]["duplicates"][0]["row"]["product_barcodes"] == ["9000000000100"]
    # no catalogue row for E414 -> reported as a content gap, nothing to repoint
    assert legacy["E414"]["canonical_target"] is None
    assert "content gap" in legacy["E414"]["note"]
    assert any("CREATE UNIQUE INDEX" in line for line in plan["unique_index_proposal_not_applied"])


@pytest.mark.asyncio
async def test_plan_flags_a_duplicate_that_holds_narrative_for_manual_review(db_session):
    await _seed(db_session)
    row = (await db_session.execute(select(Ingredient).where(Ingredient.id == OCR))).scalar_one()
    row.description = "Hand-written note worth keeping"
    await db_session.commit()

    (collision,) = (await _load(db_session))["case_collisions"]
    (dup,) = collision["duplicates"]
    assert dup["requires_manual_review"] is True
    assert dup["preserve"]["non_empty_narrative_needs_manual_review"] is True


@pytest.mark.asyncio
async def test_plan_issues_only_select_statements_and_changes_nothing(db_session):
    await _seed(db_session)
    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def _spy(conn, cursor, statement, params, context, executemany):
        statements.append(statement.lstrip().split(None, 1)[0].upper())

    async def _snapshot():
        ing = (await db_session.execute(select(Ingredient.id, Ingredient.e_number, Ingredient.description))).all()
        ali = (await db_session.execute(select(IngredientAlias.id, IngredientAlias.ingredient_id))).all()
        prod = (await db_session.execute(select(Product.barcode, Product.ingredient_ids))).all()
        return sorted(map(tuple, ing), key=str), sorted(map(tuple, ali), key=str), sorted(map(tuple, prod), key=str)

    before = await _snapshot()
    event.listen(engine, "before_cursor_execute", _spy)
    try:
        await _load(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", _spy)

    assert statements and set(statements) == {"SELECT"}
    assert await _snapshot() == before
