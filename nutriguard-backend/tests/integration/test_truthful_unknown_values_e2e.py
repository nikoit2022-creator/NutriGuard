"""
Issue #21 follow-up, END-TO-END through the real HTTP endpoints (in-memory
SQLite, Gemini/providers faked -- no network):

  * tri-state product dietary flags (`isGlutenFree`/... : true|false|null),
  * a consistent null (never a fabricated 0) `healthScore` on every
    response that embeds a product, and a genuine 0 preserved as 0,
  * honest allergens (`allergensDetected` is "" for unknown, never "None"),
  * unknown values never producing confirmed-incompatibility warnings,
  * evidence-preserving barcode+label enrichment.

The GET-product tests are the fixed-behavior counterparts of the audit's
intentional reproductions (`test_audit_zero_health_score_collision_issue21`
/ `test_audit_unknown_value_semantics`, which asserted `healthScore == 0`
for an unverified product).
"""
import io
import json
from datetime import datetime, timezone

import pytest
from PIL import Image

from app.integrations.barcode_providers.base import NutritionFacts
from app.integrations.gemini import gemini_service
from app.models.product import Product

from tests.integration.test_barcode_discovery_flow import (
    FakeProvider,
    _off_result,
    _patch_providers,
    _register_device,
)

FLAG_KEYS = ("isGlutenFree", "isLactoseFree", "isVegan", "isVegetarian", "isHalal", "isKosher")
DIETARY_WARNING_TITLES = ("Gluten Violation", "Lactose Contained", "Non-Vegan Product", "Halal Compliance Alert", "Kosher Compliance Alert")
STRICT_PROFILE = {
    "avoidGluten": True,
    "avoidLactose": True,
    "requireVegan": True,
    "requireHalal": True,
    "requireKosher": True,
}
BARCODE = "4006381333931"


async def _set_strict_profile(client, headers):
    resp = await client.put("/api/v1/health-profile", json=STRICT_PROFILE, headers=headers)
    assert resp.status_code == 200


def _dietary_titles(body: dict) -> set[str]:
    return {w["title"] for w in body["warnings"]} & set(DIETARY_WARNING_TITLES)


def _fake_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(7, 8, 9)).save(buf, format="JPEG")
    return buf.getvalue()


async def _label_image(client, headers, monkeypatch, payload: dict, barcode: str | None = None):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    files = {"image": ("label.jpg", _fake_jpeg(), "image/jpeg")}
    return await client.post(
        "/api/v1/scan/label-image", headers=headers, files=files, data={"barcode": barcode} if barcode else None
    )


# --- tri-state flags through POST /scan/ocr-text ------------------------------


@pytest.mark.asyncio
async def test_bulgarian_ocr_text_reports_unknown_flags_and_no_allergen_claim(app_client):
    headers = await _register_device(app_client, "tristate-bg")
    await _set_strict_profile(app_client, headers)

    resp = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Пшенично брашно, мляко, сол"}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    product = body["product"]

    for key in FLAG_KEYS:
        assert key in product, key  # key always present...
        assert product[key] is None, key  # ...value unknown (was True: audit Finding 1)
    assert product["allergensDetected"] == ""  # was the literal "None"
    assert body["healthScore"] is None
    assert product["healthScore"] is None  # nested product consistent with the top level


def _verified_label_payload(raw_text: str, **extra) -> dict:
    """A label extraction that verifies BOTH evidence groups, so the scan
    is scored and personalized warnings are computed (standalone
    `/scan/ocr-text` never is -- V13 -- and always returns `warnings: []`)."""
    payload = {
        "productName": "Test Product",
        "rawIngredientText": raw_text,
        "ingredients": [],
        "sugarGrams": 1.0,
        "sodiumMg": 100.0,
        "saturatedFatGrams": 1.0,
        "nutritionBasis": "PER_100_G",
    }
    payload.update(extra)
    return payload


@pytest.mark.asyncio
async def test_english_label_reports_supported_incompatibilities_and_warns_only_for_those(app_client, monkeypatch):
    headers = await _register_device(app_client, "tristate-en")
    await _set_strict_profile(app_client, headers)

    resp = await _label_image(
        app_client, headers, monkeypatch, _verified_label_payload("wheat flour, whole milk powder, salt")
    )
    assert resp.status_code == 200
    body = resp.json()
    product = body["product"]
    assert product["isVerified"] is True  # so warnings really were computed

    assert product["isGlutenFree"] is False
    assert product["isLactoseFree"] is False
    assert product["isVegan"] is False
    assert product["isVegetarian"] is None  # nothing contradicts it, nothing supports it
    assert product["isHalal"] is None
    assert product["isKosher"] is None
    # Warnings fire ONLY for the supported incompatibilities (halal/kosher are unknown -> none):
    assert _dietary_titles(body) == {"Gluten Violation", "Lactose Contained", "Non-Vegan Product"}


@pytest.mark.asyncio
async def test_unknown_flags_on_a_scored_bulgarian_label_produce_no_dietary_warning(app_client, monkeypatch):
    """The strict profile requires vegan/halal/kosher and avoids gluten/lactose;
    the label is scored (warnings ARE computed), Bulgarian, and genuinely contains
    wheat and milk that the English heuristic cannot read. The product's flags
    are UNKNOWN, so there is no confirmed-incompatibility warning -- and,
    unlike before, no false claim of compliance either."""
    headers = await _register_device(app_client, "tristate-bg-scored")
    await _set_strict_profile(app_client, headers)

    body = (
        await _label_image(
            app_client, headers, monkeypatch, _verified_label_payload("Пшенично брашно, мляко, сол")
        )
    ).json()
    assert body["product"]["isVerified"] is True
    assert all(body["product"][key] is None for key in FLAG_KEYS)
    assert _dietary_titles(body) == set()


@pytest.mark.asyncio
async def test_explicit_gemini_true_is_honored_and_never_warns(app_client, monkeypatch):
    headers = await _register_device(app_client, "tristate-explicit-true")
    await _set_strict_profile(app_client, headers)
    payload = _verified_label_payload(
        "Rice flour, water",
        isGlutenFree=True, isLactoseFree=True, isVegan=True, isVegetarian=True, isHalal=True, isKosher=True,
    )
    body = (await _label_image(app_client, headers, monkeypatch, payload)).json()
    assert all(body["product"][key] is True for key in FLAG_KEYS)
    assert _dietary_titles(body) == set()


@pytest.mark.asyncio
async def test_plain_english_text_without_evidence_is_unknown_not_suitable(app_client):
    headers = await _register_device(app_client, "tristate-plain")
    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "water, salt"}, headers=headers)
    product = resp.json()["product"]
    assert all(product[key] is None for key in FLAG_KEYS)
    assert product["allergensDetected"] == ""


# --- tri-state flags through POST /scan/label-image (Gemini structured) -------


@pytest.mark.asyncio
async def test_label_image_flags_follow_explicit_json_booleans_and_unknown_otherwise(app_client, monkeypatch):
    headers = await _register_device(app_client, "tristate-label-image")
    payload = {
        "productName": "Oat Bar",
        "rawIngredientText": "Oats, honey",
        "ingredients": [],
        "sugarGrams": 4.0,
        "sodiumMg": 50.0,
        "saturatedFatGrams": 1.0,
        "nutritionBasis": "PER_100_G",
        "isVegan": False,  # explicit false (honey) -> supported incompatibility
        "isVegetarian": True,  # explicit true
        "isGlutenFree": None,  # explicit null -> unknown
        "isLactoseFree": "true",  # malformed -> unknown
        "isHalal": 1,  # malformed -> unknown
        # isKosher missing -> unknown
    }
    resp = await _label_image(app_client, headers, monkeypatch, payload)
    assert resp.status_code == 200
    product = resp.json()["product"]
    assert product["isVegan"] is False
    assert product["isVegetarian"] is True
    assert product["isGlutenFree"] is None
    assert product["isLactoseFree"] is None
    assert product["isHalal"] is None
    assert product["isKosher"] is None
    assert product["allergensDetected"] == ""


# --- Health Score: null vs genuine 0, through real endpoints -------------------


async def _add_product(db_session, barcode: str, **overrides) -> Product:
    fields = dict(
        barcode=barcode,
        product_name=f"Row {barcode}",
        brand="Test",
        category="Test",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=None,
        nova_group=3,
        nutrition_basis="PER_100_G",
        source="local",
        is_gluten_free=None,
        is_lactose_free=None,
        is_vegan=None,
        is_vegetarian=None,
        is_halal=None,
        is_kosher=None,
        is_verified=False,
        has_verified_nutrition=False,
        has_verified_ingredients=False,
        last_verified_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    product = Product(**fields)
    db_session.add(product)
    await db_session.commit()
    return product


@pytest.mark.asyncio
async def test_get_product_reports_null_health_score_for_an_unverified_product_even_with_legacy_zero(
    app_client, db_session
):
    """Fix for audit Finding 2. A legacy/unmigrated row holding the old
    placeholder `0` in an UNVERIFIED product must not surface as a score."""
    headers = await _register_device(app_client, "score-unverified")
    await _add_product(db_session, "9990000000001", health_score=0)  # exact legacy placeholder
    await _add_product(db_session, "9990000000002", health_score=None)

    for barcode in ("9990000000001", "9990000000002"):
        resp = await app_client.get(f"/api/v1/products/{barcode}", headers=headers)
        assert resp.status_code == 200
        product = resp.json()["product"]
        assert "healthScore" in product
        assert product["healthScore"] is None, barcode
        assert product["isVerified"] is False

    listing = await app_client.get("/api/v1/products?search=Row", headers=headers)
    assert [p["healthScore"] for p in listing.json()["items"]] == [None, None]


@pytest.mark.asyncio
async def test_get_product_preserves_a_genuine_zero_and_real_scores_for_verified_products(app_client, db_session):
    headers = await _register_device(app_client, "score-verified")
    verified = dict(is_verified=True, has_verified_nutrition=True, has_verified_ingredients=True)
    await _add_product(db_session, "9990000000011", health_score=0, **verified)  # genuine worst score
    await _add_product(db_session, "9990000000012", health_score=63, **verified)

    zero = (await app_client.get("/api/v1/products/9990000000011", headers=headers)).json()["product"]
    real = (await app_client.get("/api/v1/products/9990000000012", headers=headers)).json()["product"]
    assert zero["healthScore"] == 0  # NOT collapsed to null
    assert zero["isVerified"] is True
    assert real["healthScore"] == 63


@pytest.mark.asyncio
async def test_scan_barcode_genuine_computed_zero_is_zero_at_top_level_and_nested(app_client, db_session):
    """A REAL computed score of 0 (every deduction maxed) survives both the
    top-level `healthScore` and the nested `product.healthScore`."""
    headers = await _register_device(app_client, "score-genuine-zero")
    await _add_product(
        db_session,
        "9990000000021",
        health_score=None,  # never scored yet
        sugar_grams=30.0,
        sodium_mg=1000.0,
        saturated_fat_grams=9.0,
        has_artificial_sweeteners=True,
        has_preservatives=True,
        nova_group=4,
        is_verified=True,
        has_verified_nutrition=True,
        has_verified_ingredients=True,
    )
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": "9990000000021"}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthScore"] == 0
    assert body["product"]["healthScore"] == 0

    # ...and the plain lookup afterwards agrees (score persisted as a real 0).
    again = (await app_client.get("/api/v1/products/9990000000021", headers=headers)).json()["product"]
    assert again["healthScore"] == 0


@pytest.mark.asyncio
async def test_scan_ocr_text_success_path_has_consistent_null_score_at_both_levels(app_client):
    """Partial-success path: ingredients recognized, nutrition never verified."""
    headers = await _register_device(app_client, "score-ocr-partial")
    body = (
        await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar, Salt"}, headers=headers)
    ).json()
    assert body["healthScore"] is None
    assert body["product"]["healthScore"] is None
    assert body["product"]["hasVerifiedNutrition"] is False


@pytest.mark.asyncio
async def test_verified_label_image_scan_returns_the_real_score_at_both_levels(app_client, monkeypatch):
    headers = await _register_device(app_client, "score-label-verified")
    payload = {
        "productName": "Plain Water Crackers",
        "rawIngredientText": "Flour, water, salt",
        "ingredients": [],
        "sugarGrams": 1.0,
        "sodiumMg": 100.0,
        "saturatedFatGrams": 1.0,
        "nutritionBasis": "PER_100_G",
    }
    body = (await _label_image(app_client, headers, monkeypatch, payload)).json()
    assert isinstance(body["healthScore"], int)
    assert body["product"]["isVerified"] is True
    assert body["product"]["healthScore"] == body["healthScore"]


# --- discovery: unknown provider flags are unknown, not False ------------------


@pytest.mark.asyncio
async def test_discovered_product_flags_follow_provider_tags_and_unknown_otherwise(app_client, monkeypatch):
    off = FakeProvider(
        "open_food_facts",
        0.75,
        _off_result(
            raw_ingredient_text="Wheat flour, sugar, water",
            dietary_flags={"is_vegan": True, "is_kosher": False},
        ),
    )
    _patch_providers(monkeypatch, off=off)
    headers = await _register_device(app_client, "tristate-discovery")

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": BARCODE}, headers=headers)
    assert resp.status_code == 200
    product = resp.json()["product"]
    assert product["isVegan"] is True  # provider's explicit tag
    assert product["isKosher"] is False  # provider's explicit false
    assert product["isGlutenFree"] is False  # provider silent, but "wheat" in its ingredient text
    assert product["isLactoseFree"] is None  # provider silent, no evidence: unknown (was False)
    assert product["isHalal"] is None
    assert product["isVegetarian"] is None
    assert product["allergensDetected"] == ""  # provider listed none -> unknown, never "None"


# --- evidence-preserving enrichment through the real endpoint -------------------


@pytest.mark.asyncio
async def test_barcode_label_enrichment_with_unknown_flags_keeps_supported_provider_evidence(
    app_client, monkeypatch
):
    off = FakeProvider(
        "open_food_facts",
        0.75,
        _off_result(
            raw_ingredient_text="Water, Sugar",
            nutrition=NutritionFacts(sugar_grams=12.0, sodium_mg=None, saturated_fat_grams=None),  # incomplete
            dietary_flags={"is_vegan": True},
            allergens=["Milk"],
        ),
    )
    _patch_providers(monkeypatch, off=off)
    headers = await _register_device(app_client, "tristate-enrich")

    first = await app_client.post("/api/v1/scan/barcode", json={"barcode": BARCODE}, headers=headers)
    assert first.status_code == 404  # persisted but incomplete -> labelScanRequired
    stored = (await app_client.get(f"/api/v1/products/{BARCODE}", headers=headers)).json()["product"]
    assert stored["isVegan"] is True
    assert stored["allergensDetected"] == "Milk"

    # A Bulgarian label the heuristic cannot read: incoming flags/allergens all UNKNOWN.
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Вода, захар, сол", "barcode": BARCODE},
        headers=headers,
    )
    assert resp.status_code == 200
    product = resp.json()["product"]
    assert product["rawIngredientText"] == "Вода, захар, сол"  # the ingredients group did refresh
    assert product["isVegan"] is True  # supported provider evidence NOT erased by an unknown
    assert product["allergensDetected"] == "Milk"
    assert product["isGlutenFree"] is None

    # Newer, explicit evidence (an English label naming pork) DOES replace it:
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, sugar, pork gelatin", "barcode": BARCODE},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["product"]["isVegan"] is False
    assert resp.json()["product"]["isHalal"] is False
