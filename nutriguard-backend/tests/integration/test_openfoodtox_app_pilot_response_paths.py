"""docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md requirement 4: serve
EN/BG content for the four owner-approved OpenFoodTox pilot identities
consistently through direct ingredient, product, OCR-text and
partial-result serialization paths (label-image is already covered end
to end for the general owner-approved mechanism by
test_ingredient_localization_response_paths.py's REVIEWED case -- this
file's job is to prove the actual pilot import's OWNER-APPROVED DRAFT
content reaches every path honestly labeled, not to re-prove the
label-image pipeline itself).

Runs the REAL seed + pilot import (monkeypatched onto the disposable
`db_engine`, same pattern as test_openfoodtox_pilot_import.py) so these
are the actual bytes a client would receive, not hand-built fixtures.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.product import Product
from app.seed import load_openfoodtox_pilot_content as import_module
from app.seed import load_seed as load_seed_module
from app.services.food_analysis import _label_scan_required_details, fetch_ingredients_for_product


@pytest.fixture
def session_factory(db_engine, monkeypatch):
    factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(import_module, "AsyncSessionLocal", factory)
    return factory


@pytest.fixture
async def pilot_seeded(session_factory):
    await load_seed_module.load_seed()
    assert await import_module.run(apply=True) == 0
    return session_factory


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def _assert_owner_approved_bg(localizations: dict) -> None:
    bg = localizations["bg"]
    assert bg["translationStatus"] == "DRAFT"
    assert bg["ownerApprovedWithoutReview"] is True
    assert bg["description"]
    assert localizations["en"]["ownerApprovedWithoutReview"] is False


# --- 1. Direct ingredient endpoints (GET /ingredients/{id}, GET /ingredients) ---


@pytest.mark.asyncio
async def test_direct_ingredient_get_serves_owner_approved_content_for_new_e150d(app_client, pilot_seeded):
    headers = await _register_device(app_client, "pilot-direct-get-device")

    resp = await app_client.get("/api/v1/ingredients/e150d_sulphite_ammonia_caramel", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["eNumber"] == "E150d"
    _assert_owner_approved_bg(body["localizations"])
    # No draft label, internal quote or operator-only text leaks into display text.
    for forbidden in ("DRAFT", "operator", "not_reviewed", "internal"):
        assert forbidden not in body["description"]
        assert forbidden not in body["healthConcerns"]


@pytest.mark.asyncio
async def test_ingredient_listing_serves_owner_approved_content_for_updated_e250(app_client, pilot_seeded):
    headers = await _register_device(app_client, "pilot-listing-device")

    resp = await app_client.get(
        "/api/v1/ingredients", headers=headers, params={"search": "Sodium Nitrite"}
    )
    assert resp.status_code == 200
    body = resp.json()
    ingredient = next(i for i in body["items"] if i["id"] == "e250_sodium_nitrite")
    _assert_owner_approved_bg(ingredient["localizations"])
    # Preserved exactly -- the pilot never touches these.
    assert ingredient["riskLevel"] == "HIGH_CONCERN"
    assert ingredient["riskAssessmentAvailable"] is True


# --- 2. Product detail (GET /products/{barcode}) --------------------------


@pytest.mark.asyncio
async def test_product_detail_serves_owner_approved_content_for_new_ingredient(app_client, pilot_seeded, session_factory):
    async with session_factory() as db:
        product = Product(
            barcode="9990000000100",
            product_name="Cola-type soft drink",
            brand="TestBrand",
            category="Beverage",
            raw_ingredient_text="Water, Sugar, Caramel colour (E150d)",
            ingredient_ids="e150d_sulphite_ammonia_caramel",
            health_score=70,
            nova_group=4,
            sugar_grams=10,
            sodium_mg=5,
            saturated_fat_grams=0,
            allergens_detected="None",
            source="local",
            is_verified=True,
            has_verified_nutrition=True,
            has_verified_ingredients=True,
        )
        db.add(product)
        await db.commit()

    headers = await _register_device(app_client, "pilot-product-detail-device")
    resp = await app_client.get("/api/v1/products/9990000000100", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    ingredient = next(i for i in body["ingredients"] if i["id"] == "e150d_sulphite_ammonia_caramel")
    _assert_owner_approved_bg(ingredient["localizations"])


# --- 3. OCR-text scan path -------------------------------------------------


@pytest.mark.asyncio
async def test_ocr_text_scan_resolves_pilot_identity_and_serves_owner_approved_content(app_client, pilot_seeded):
    headers = await _register_device(app_client, "pilot-ocr-text-device")

    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Ingredients: Carbonated water, Sugar, Caramel colour (E150d), Phosphoric acid"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    ingredients = body["product"]["ingredients"] if "ingredients" in body.get("product", {}) else body["ingredients"]
    matched = [i for i in ingredients if i.get("eNumber") == "E150d"]
    assert matched, [i.get("eNumber") or i.get("commonName") for i in ingredients]
    _assert_owner_approved_bg(matched[0]["localizations"])


# --- 4. Partial (labelScanRequired) hand-built response --------------------


@pytest.mark.asyncio
async def test_partial_result_serves_owner_approved_content(pilot_seeded, session_factory):
    async with session_factory() as db:
        product = Product(
            barcode="9990000000117",
            product_name="Discovered But Incomplete Cola",
            brand="TestBrand",
            category="Beverage",
            raw_ingredient_text="Water, Sugar, Caramel colour (E150d)",
            ingredient_ids="e150d_sulphite_ammonia_caramel",
            health_score=0,
            nova_group=1,
            allergens_detected="None",
            source="open_food_facts",
            is_verified=False,
            has_verified_nutrition=False,
            has_verified_ingredients=True,
        )
        db.add(product)
        await db.commit()

        ingredients = await fetch_ingredients_for_product(db, product)
        details = _label_scan_required_details(product, ingredients)

    assert details["labelScanRequired"] is True
    partial_ingredient = next(i for i in details["ingredients"] if i["id"] == "e150d_sulphite_ammonia_caramel")
    _assert_owner_approved_bg(partial_ingredient["localizations"])
