"""
Integration-branch regression: the ingredient-identity read path and the
scan-attempt diagnostics work TOGETHER without changing catalogue content.

A product persisted with a LEGACY Cyrillic-E id is reloaded with scan-attempt
headers and a real journal enabled; the response must reach the curated row
with its description, reviewed BG localization and scientific/scoring flags
exactly as stored, the stored catalogue row must be byte-for-byte untouched,
bare numbers and ambiguous ids must stay fail-closed, and the attempt id must
correlate the backend journal lines.
"""
import json

import pytest
from sqlalchemy import select

from app.core import scan_diagnostics
from app.models.enums import (
    IngredientSource,
    IngredientTranslationSource,
    IngredientTranslationStatus,
    IngredientVerificationStatus,
    RiskLevel,
)
from app.models.ingredient import Ingredient
from app.models.ingredient_localization import IngredientLocalization
from app.models.product import Product
from app.services.ingredient_localization import canonical_text_hash
from app.services.ocr_normalizer import _synthetic_id

CYR_E = "Е"
ATTEMPT = "4444444444440001"


@pytest.fixture(autouse=True)
def _journal(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    yield tmp_path / "scan.jsonl"


def _curated_e330() -> Ingredient:
    return Ingredient(
        id="e330_citric_acid", common_name="Citric acid", normalized_name="citric acid", scientific_name="citric acid",
        e_number="E330", category="Acidity regulator", description="Weak organic acid used as acidity regulator.",
        purpose_in_food="Acidity regulator", health_concerns="No concern at typical intakes.",
        evidence_level="Reviewed", countries_restricted_or_banned="", efsa_status="Authorised", fda_status="GRAS",
        acceptable_daily_intake="Not specified", side_effects="", allergens="", references="EFSA 2015",
        risk_level=RiskLevel.SAFE, risk_assessment_available=True, bad_for_diabetes=False,
        verification_status=IngredientVerificationStatus.LIMITED_DATA, source=IngredientSource.CURATED_SEED,
        confidence=0.9,
    )


def _row_snapshot(row: Ingredient) -> dict:
    return {c.name: getattr(row, c.name) for c in Ingredient.__table__.columns}


def _product(barcode: str, raw: str, ids: str) -> Product:
    return Product(
        barcode=barcode, product_name="Legacy Drink", brand="", category="", raw_ingredient_text=raw, ingredient_ids=ids,
        health_score=50, nova_group=4, sugar_grams=1, sodium_mg=10, saturated_fat_grams=0, allergens_detected="",
        source="label_scan", is_verified=True, has_verified_nutrition=True, has_verified_ingredients=True,
        nutrition_basis="PER_100_ML",
    )


@pytest.mark.asyncio
async def test_legacy_reload_with_attempt_headers_preserves_catalogue_content(app_client, db_session, _journal):
    ing = _curated_e330()
    db_session.add(ing)
    await db_session.flush()
    db_session.add(
        IngredientLocalization(
            ingredient_id=ing.id, language="bg", common_name="Лимонена киселина", category="Регулатор на киселинността",
            description="Слаба органична киселина.", purpose_in_food="Регулатор на киселинността",
            translation_status=IngredientTranslationStatus.REVIEWED, translation_source=IngredientTranslationSource.HUMAN_CURATED,
            source_content_hash=canonical_text_hash(ing),
        )
    )
    legacy_e330 = _synthetic_id(f"{CYR_E}330")  # generation-2 id of the UNFOLDED token
    bare = _synthetic_id("330")
    db_session.add(_product("8200990011223", f"Вода, {CYR_E}330, 330, вода, сол", f"{legacy_e330},{bare},synth_"))
    await db_session.commit()
    db_session.expire_all()  # snapshot what the database holds (Decimal etc.), not the constructor values
    before = _row_snapshot(
        (await db_session.execute(select(Ingredient).where(Ingredient.id == 'e330_citric_acid'))).scalar_one()
    )

    reg = await app_client.post("/api/v1/auth/device", json={"deviceId": "integration-together"})
    headers = {"Authorization": f"Bearer {reg.json()['accessToken']}", "X-Scan-Attempt-Id": ATTEMPT, "X-Scan-Request-Sequence": "2"}
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": "8200990011223"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Scan-Attempt-Id"] == ATTEMPT
    out = resp.json()["ingredients"]

    canonical = out[0]
    assert canonical["id"] == "e330_citric_acid"
    assert canonical["description"] == before["description"]
    assert canonical["healthConcerns"] == before["health_concerns"]
    assert canonical["riskLevel"] == "SAFE" and canonical["riskAssessmentAvailable"] is True
    assert canonical["localizations"]["bg"]["commonName"] == "Лимонена киселина"  # reviewed BG localization still served
    # bare number and the collapsed/ambiguous id fail closed
    assert out[1]["commonName"] == "330" and not out[1].get("eNumber")
    assert out[2]["commonName"] == "Ingredient detected on label"

    db_session.expire_all()
    after = (await db_session.execute(select(Ingredient).where(Ingredient.id == "e330_citric_acid"))).scalar_one()
    assert _row_snapshot(after) == before  # catalogue row byte-for-byte unchanged
    stored = (await db_session.execute(select(Product).where(Product.barcode == "8200990011223"))).scalar_one()
    assert stored.ingredient_ids == f"{legacy_e330},{bare},synth_"  # product ids never rewritten

    lines = [json.loads(x) for x in _journal.read_text(encoding="utf-8").splitlines()]
    mine = [r for r in lines if r.get("scanAttemptId") == ATTEMPT]
    assert mine and all(r["requestSequence"] == 2 and r["origin"] == "backend" for r in mine)
    assert any("pipelineEvent" in r for r in mine)
