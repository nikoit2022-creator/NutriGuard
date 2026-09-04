"""
Regression tests for V13: ingredient-recognition success is now the
primary success condition for a label-driven scan (`/scan/label-image`,
`/scan/ocr-text`, and their barcode-linked variants), decoupled from
full nutrition verification / Health-Score readiness. See the "V13"
entry in `app/services/food_analysis.py`'s module docstring and README
section 11.14 for the full design rationale.

This file exercises the specific scenarios called out in that change:
  1. an ingredients-only label image succeeds (`200`, not `labelScanRequired`)
  2. a barcode-linked ingredients-only image enriches the product BY BARCODE
  3. nutrition remains optional for this flow, across all three call shapes
  4. Health Score is produced only when nutrition is ALSO reliably known
  5. a true upstream image-analysis failure still does not fabricate success

Lower-level field-safety/merge-semantics coverage for the same change
lives in `tests/integration/test_label_barcode_enrichment.py` (which
also already covered most of this before V13, under the OLD, now
corrected, expectations) -- this file is the single place that pins the
end-to-end CONTRACT in one read.

Every Gemini call is mocked -- no test here makes a live network call.
"""
import io
import json

import pytest
from PIL import Image
from sqlalchemy import select

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.product import Product
from app.models.product_source import ProductSource

# Checksum-valid EAN-13 barcodes not used elsewhere in the suite.
BARCODE_STANDALONE_INGREDIENTS_ONLY = "1822782489632"
BARCODE_LINKED_INGREDIENTS_ONLY = "8346578713310"
BARCODE_NUTRITION_OPTIONAL_LABEL = "5098393010312"
BARCODE_NUTRITION_OPTIONAL_LINKED = "0518347382999"
BARCODE_COMPLETE_FOR_SCORE = "7376311656674"
BARCODE_INCOMPLETE_FOR_SCORE = "0106513338729"
BARCODE_TRUE_FAILURE_LINKED = "6247317810807"


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(50, 60, 70)).save(buf, format="JPEG")
    return buf.getvalue()


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _upload(client, headers, *, barcode: str | None = None):
    files = {"image": ("label.jpg", _fake_jpeg_bytes(), "image/jpeg")}
    data = {"barcode": barcode} if barcode is not None else None
    return client.post("/api/v1/scan/label-image", headers=headers, files=files, data=data)


def _full_gemini_payload(**overrides) -> dict:
    """A complete, trustworthy Gemini image-analysis response: real
    ingredients AND real nutrition. Individual fields are nulled/omitted
    per test to model an "ingredients-only" photo (a real nutrition
    panel simply wasn't in frame)."""
    payload = {
        "productName": "Harvest Grain Bar",
        "brand": "Harvest Co",
        "sugarGrams": 9.0,
        "sodiumMg": 55.0,
        "saturatedFatGrams": 1.2,
        "nutritionBasis": "PER_100_G",
        "hasArtificialSweeteners": False,
        "hasPreservatives": True,
        "isGlutenFree": False,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        # "Contains:" is a real label word and a STRONG English-detection
        # signal (see `language_detection._ENGLISH_STRONG_WORDS`) that
        # `ocr_normalizer.normalize_and_extract_tokens` strips before
        # ingredient matching -- keeps the barcode-linked (strict-mode)
        # cases below from spuriously requiring an (unmocked) AI
        # translation call.
        "rawIngredientText": "Contains: Oats, Sugar, Sodium Benzoate (E211), Citric Acid",
        "ingredients": [
            {"commonName": "Oats"},
            {"commonName": "Sodium Benzoate", "eNumber": "E211"},
            {"commonName": "Citric Acid"},
        ],
    }
    payload.update(overrides)
    return payload


def _mock_gemini_image(monkeypatch, **overrides) -> dict:
    payload = _full_gemini_payload(**overrides)

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    return payload


# --- 1. An ingredients-only label image succeeds ---------------------------


@pytest.mark.asyncio
async def test_standalone_label_image_ingredients_only_succeeds(app_client, monkeypatch, db_session):
    """A label photo that clearly shows the ingredients list but not the
    nutrition panel -- the everyday case -- is a normal `200` success,
    not `labelScanRequired`, as long as ingredient recognition itself
    succeeded."""
    _mock_gemini_image(monkeypatch, sugarGrams=None, sodiumMg=None, saturatedFatGrams=None)
    headers = await _register_device(app_client, "ingredients-only-standalone-device")

    resp = await _upload(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()

    assert body["product"]["productName"] == "Harvest Grain Bar"
    assert "Oats" in body["product"]["rawIngredientText"]
    names = {ing["commonName"] for ing in body["ingredients"]}
    assert "Oats" in names
    # Categorization ran: real, scientific-database-shaped entries, not
    # a truncated summary -- the ingredient-recognition/categorization
    # pipeline (`ocr_normalizer`/`ingredient_repository`) fully applied.
    assert all("riskLevel" in ing and "category" in ing for ing in body["ingredients"])

    assert body["product"]["hasVerifiedIngredients"] is True
    assert body["product"]["hasVerifiedNutrition"] is False
    assert body["product"]["isVerified"] is False


# --- 2. A barcode-linked ingredients-only image enriches BY BARCODE -------


@pytest.mark.asyncio
async def test_barcode_linked_ingredients_only_image_enriches_product_by_barcode(
    app_client, monkeypatch, db_session
):
    """When a barcode is visible/supplied alongside the label image, it
    is used as the product identity key -- the product is created/
    enriched under that EXACT barcode, not a synthetic `img_...` id --
    even though the nutrition panel wasn't captured."""
    _mock_gemini_image(monkeypatch, sugarGrams=None, sodiumMg=None, saturatedFatGrams=None)
    headers = await _register_device(app_client, "ingredients-only-linked-device")

    resp = await _upload(app_client, headers, barcode=BARCODE_LINKED_INGREDIENTS_ONLY)
    assert resp.status_code == 200
    body = resp.json()

    # Barcode used as the identity key, not a synthetic id.
    assert body["product"]["barcode"] == BARCODE_LINKED_INGREDIENTS_ONLY
    assert body["healthScore"] is None
    assert body["product"]["hasVerifiedIngredients"] is True
    assert body["product"]["hasVerifiedNutrition"] is False

    # Persisted under that barcode: exactly one product row, ingredient
    # categorization (ingredient_ids) recorded on it, and provenance
    # recorded against the same barcode.
    stored = await db_session.get(Product, BARCODE_LINKED_INGREDIENTS_ONLY)
    assert stored is not None
    assert stored.ingredient_ids != ""
    assert stored.has_verified_ingredients is True
    assert stored.has_verified_nutrition is False

    sources = (
        await db_session.execute(
            select(ProductSource).where(
                ProductSource.barcode == BARCODE_LINKED_INGREDIENTS_ONLY,
                ProductSource.provider == "label_ocr",
            )
        )
    ).scalars().all()
    assert len(sources) == 1


# --- 3. Nutrition remains optional across all three call shapes -----------


@pytest.mark.asyncio
async def test_nutrition_remains_optional_for_label_driven_scans(app_client, monkeypatch, db_session):
    """Missing/incomplete nutrition extraction must not fail the scan,
    whether it's a standalone label image, a barcode-linked label image,
    or standalone OCR text (whose nutrition is ALWAYS a heuristic guess,
    never genuinely verified -- see `analyze_ocr_text`'s docstring)."""
    headers = await _register_device(app_client, "nutrition-optional-device")

    _mock_gemini_image(monkeypatch, sodiumMg=None)
    standalone_resp = await _upload(app_client, headers)
    assert standalone_resp.status_code == 200
    assert standalone_resp.json()["healthScore"] is None

    _mock_gemini_image(monkeypatch, saturatedFatGrams=float("nan"))
    linked_resp = await _upload(app_client, headers, barcode=BARCODE_NUTRITION_OPTIONAL_LINKED)
    assert linked_resp.status_code == 200
    assert linked_resp.json()["healthScore"] is None
    assert linked_resp.json()["product"]["barcode"] == BARCODE_NUTRITION_OPTIONAL_LINKED

    ocr_resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Oats, Sugar, Sodium Benzoate (E211)"},
        headers=headers,
    )
    assert ocr_resp.status_code == 200
    assert ocr_resp.json()["healthScore"] is None
    assert ocr_resp.json()["product"]["hasVerifiedIngredients"] is True


# --- 4. Health Score is produced only when nutrition is ALSO known --------


@pytest.mark.asyncio
async def test_health_score_only_produced_when_nutrition_is_sufficiently_reliable(
    app_client, monkeypatch, db_session
):
    """Same ingredient-recognition success either way; the Health Score
    is the one thing that depends on nutrition completeness."""
    headers = await _register_device(app_client, "health-score-gating-device")

    _mock_gemini_image(monkeypatch)  # complete: real ingredients AND real nutrition
    complete_resp = await _upload(app_client, headers, barcode=BARCODE_COMPLETE_FOR_SCORE)
    assert complete_resp.status_code == 200
    assert isinstance(complete_resp.json()["healthScore"], int)
    assert complete_resp.json()["product"]["isVerified"] is True

    _mock_gemini_image(monkeypatch, sugarGrams=None)  # ingredients only
    incomplete_resp = await _upload(app_client, headers, barcode=BARCODE_INCOMPLETE_FOR_SCORE)
    assert incomplete_resp.status_code == 200
    assert incomplete_resp.json()["healthScore"] is None
    assert incomplete_resp.json()["product"]["isVerified"] is False

    # A scan-history entry (which always carries a real score) is only
    # ever written for the complete product, never the incomplete one.
    history = await app_client.get("/api/v1/scan-history", headers=headers)
    history_barcodes = {item["barcode"] for item in history.json()["items"]}
    assert BARCODE_COMPLETE_FOR_SCORE in history_barcodes
    assert BARCODE_INCOMPLETE_FOR_SCORE not in history_barcodes


# --- 5. A true upstream image-analysis failure still fails ----------------


@pytest.mark.asyncio
async def test_true_image_analysis_failure_does_not_fabricate_success(app_client, monkeypatch, db_session):
    """Gemini unavailable AND no barcode/provider fallback ingredients
    -- i.e. no REAL ingredients were ever extracted -- must still return
    the structured `labelScanRequired` failure, standalone or
    barcode-linked, exactly as before V13. This is the one case V13
    deliberately leaves failing."""
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated total upstream failure")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    headers = await _register_device(app_client, "true-failure-device")

    standalone_resp = await _upload(app_client, headers)
    assert standalone_resp.status_code == 404
    standalone_error = standalone_resp.json()["error"]
    assert standalone_error["code"] == "PRODUCT_NOT_FOUND"
    assert standalone_error["details"]["labelScanRequired"] is True
    assert standalone_error["details"]["ingredientsScanRequired"] is True

    linked_resp = await _upload(app_client, headers, barcode=BARCODE_TRUE_FAILURE_LINKED)
    assert linked_resp.status_code == 404
    linked_error = linked_resp.json()["error"]
    assert linked_error["code"] == "PRODUCT_NOT_FOUND"
    assert linked_error["details"]["labelScanRequired"] is True
    assert linked_error["details"]["ingredientsScanRequired"] is True

    # The identity is still persisted (so a later scan doesn't redo
    # provider/label work from scratch), but genuinely unverified --
    # never a fabricated success.
    stored = await db_session.get(Product, BARCODE_TRUE_FAILURE_LINKED)
    assert stored is not None
    assert stored.has_verified_ingredients is False
