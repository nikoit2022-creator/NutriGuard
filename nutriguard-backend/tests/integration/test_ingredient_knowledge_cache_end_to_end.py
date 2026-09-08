"""
End-to-end HTTP coverage for the persistent ingredient knowledge cache
(task: "persistent ingredient knowledge cache") -- proves the whole
pipeline through the real `/scan/ocr-text` and `/ingredients` endpoints,
not just the service layer (see `tests/integration/test_ingredient_catalog.py`
for the lower-level repository/service tests).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models.enums import IngredientVerificationStatus
from app.models.ingredient import Ingredient
from app.models.product import Product


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


# --- 6. Product relationships: one canonical ingredient, many products -----


@pytest.mark.asyncio
async def test_one_canonical_ingredient_is_reused_across_two_different_products(app_client, db_session):
    headers = await _register_device(app_client, "knowledge-cache-device-1")

    resp_a = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Xylitol, Salt"}, headers=headers
    )
    resp_b = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Xylitol, Cocoa, Vanilla"}, headers=headers
    )
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200

    barcode_a = resp_a.json()["product"]["barcode"]
    barcode_b = resp_b.json()["product"]["barcode"]
    assert barcode_a != barcode_b  # two distinct products

    ids_a = {ing["id"] for ing in resp_a.json()["ingredients"]}
    ids_b = {ing["id"] for ing in resp_b.json()["ingredients"]}
    shared = ids_a & ids_b
    assert len(shared) == 1
    shared_id = next(iter(shared))
    assert "xylitol" in shared_id

    # Exactly ONE row in the catalog for it -- not one per product.
    count = (
        await db_session.execute(select(Ingredient).where(Ingredient.id == shared_id))
    ).scalars().all()
    assert len(count) == 1

    stored_a = await db_session.get(Product, barcode_a)
    stored_b = await db_session.get(Product, barcode_b)
    assert shared_id in stored_a.ingredient_ids.split(",")
    assert shared_id in stored_b.ingredient_ids.split(",")


@pytest.mark.asyncio
async def test_no_fabricated_scientific_data_for_the_shared_unverified_ingredient(app_client):
    headers = await _register_device(app_client, "knowledge-cache-device-2")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Unobtainium Extract"}, headers=headers
    )
    assert resp.status_code == 200
    ingredients = {ing["commonName"]: ing for ing in resp.json()["ingredients"]}
    unknown = ingredients["Unobtainium Extract"]
    assert unknown["riskAssessmentAvailable"] is False
    assert unknown["verificationStatus"] == "UNVERIFIED"
    assert unknown["description"] == ""
    assert unknown["healthConcerns"] == ""
    assert unknown["efsaApprovalStatus"] == "NO_INFORMATION"
    assert unknown["fdaApprovalStatus"] == "NO_INFORMATION"
    assert unknown["adiMinMgPerKgBwPerDay"] is None


# --- Updated catalog data becomes visible on next retrieval (req. 6) -------


@pytest.mark.asyncio
async def test_updated_catalog_ingredient_is_visible_to_every_linked_product_on_next_retrieval(
    app_client, db_session
):
    """The catalog is promoted from UNVERIFIED to VERIFIED with real
    data (simulating a future curation/regulatory-lookup pass) --
    without touching either product row -- and BOTH products must see
    the update on their next read, since neither one stores a copy of
    the scientific profile."""
    headers = await _register_device(app_client, "knowledge-cache-device-3")

    resp_a = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Glycyrrhizin"}, headers=headers
    )
    resp_b = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Glycyrrhizin, Sugar"}, headers=headers
    )
    shared_id = next(iter(
        {i["id"] for i in resp_a.json()["ingredients"]} & {i["id"] for i in resp_b.json()["ingredients"]}
    ))

    row = await db_session.get(Ingredient, shared_id)
    row.description = "Now-curated description."
    row.health_concerns = "A real, verified concern."
    row.verification_status = IngredientVerificationStatus.VERIFIED
    row.risk_assessment_available = True
    row.last_verified_at = datetime.now(timezone.utc)
    await db_session.commit()

    barcode_a = resp_a.json()["product"]["barcode"]
    barcode_b = resp_b.json()["product"]["barcode"]
    reread_a = await app_client.get(f"/api/v1/products/{barcode_a}", headers=headers)
    reread_b = await app_client.get(f"/api/v1/products/{barcode_b}", headers=headers)
    assert reread_a.status_code == 200
    assert reread_b.status_code == 200

    for resp in (reread_a, reread_b):
        ing = next(i for i in resp.json()["ingredients"] if i["id"] == shared_id)
        assert ing["description"] == "Now-curated description."
        assert ing["riskAssessmentAvailable"] is True
        assert ing["verificationStatus"] == "VERIFIED"


# --- Fresh vs. expired cache (needsRefresh) --------------------------------


@pytest.mark.asyncio
async def test_needs_refresh_is_false_for_a_freshly_verified_ingredient(app_client, db_session, monkeypatch):
    headers = await _register_device(app_client, "knowledge-cache-device-4")
    # A seeded curated row (e.g. e951_aspartame) only exists once the
    # seed loader has run -- this environment's test DB is never seeded
    # (see conftest) -- so insert a freshly-verified row directly.
    from app.models.enums import IngredientSource, RiskLevel

    fresh = Ingredient(
        id="fresh_test_ingredient",
        common_name="Fresh Test Ingredient",
        normalized_name="fresh test ingredient",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
        last_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(fresh)
    await db_session.commit()

    resp = await app_client.get("/api/v1/ingredients/fresh_test_ingredient", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["needsRefresh"] is False


@pytest.mark.asyncio
async def test_needs_refresh_is_true_once_past_the_verified_data_ttl(app_client, db_session):
    from app.core.config import settings
    from app.models.enums import IngredientSource, RiskLevel

    headers = await _register_device(app_client, "knowledge-cache-device-5")
    stale = Ingredient(
        id="stale_test_ingredient",
        common_name="Stale Test Ingredient",
        normalized_name="stale test ingredient",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
        last_verified_at=datetime.now(timezone.utc)
        - timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS + 3600),
    )
    db_session.add(stale)
    await db_session.commit()

    resp = await app_client.get("/api/v1/ingredients/stale_test_ingredient", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["needsRefresh"] is True
    # Still serves the last known value -- staleness never blocks a read.
    assert body["commonName"] == "Stale Test Ingredient"
