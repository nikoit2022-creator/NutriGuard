"""
Integration regression tests for the V3 fix: `POST /api/v1/scan/label-image`
must actually use a successful Gemini JSON response instead of discarding
it, while the fallback chain (Gemini failure -> deterministic fallback ->
AI_SERVICE_UNAVAILABLE) keeps working exactly as before.
"""
import io
import json

import pytest
from PIL import Image

from app.integrations.gemini import GeminiUnavailableError, gemini_service


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(255, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


async def _register_device(client, device_id="label-image-device"):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _upload(client, headers):
    files = {"image": ("label.jpg", _fake_jpeg_bytes(), "image/jpeg")}
    return client.post("/api/v1/scan/label-image", headers=headers, files=files)


# 1. Valid Gemini image JSON is actually used ------------------------------

@pytest.mark.asyncio
async def test_valid_gemini_json_is_used_end_to_end(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Zero Sugar Energy Drink",
        "brand": "Bolt Beverages",
        "sugarGrams": 0.0,
        "sodiumMg": 200.0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100_ML",
        "hasArtificialSweeteners": True,
        "hasPreservatives": True,
        "isGlutenFree": True,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        "rawIngredientText": "Carbonated Water, Aspartame (E951), Sodium Benzoate, Caffeine",
        "ingredients": [
            {"commonName": "Aspartame", "eNumber": "E951"},
            {"commonName": "Sodium Benzoate", "eNumber": "E211"},
            {"commonName": "Caffeine"},
        ],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "valid-gemini-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()

    # Gemini's product-level fields are preserved verbatim.
    assert body["product"]["productName"] == "Zero Sugar Energy Drink"
    assert body["product"]["brand"] == "Bolt Beverages"
    assert body["product"]["sugarGrams"] == 0.0
    assert body["product"]["sodiumMg"] == 200.0
    assert body["product"]["hasArtificialSweeteners"] is True
    assert body["product"]["hasPreservatives"] is True
    assert body["product"]["novaGroup"] == 4
    assert body["product"]["rawIngredientText"] == gemini_payload["rawIngredientText"]

    # Ingredients were normalized (3 distinct ingredients from the array).
    assert len(body["ingredients"]) == 3

    # Health Score reflects the Gemini-derived data via the UNCHANGED
    # formula: additives (3 unmatched -> synthetic risk heuristics) +
    # preservative flag (10) + nova4 (20), sugar/sodium/satfat per Gemini.
    # We don't hand-compute the exact figure here (covered precisely in
    # the V2 barcode regression tests); we assert it's a valid, non-default
    # score consistent with a heavily-processed product, not the naive
    # "everything unknown" fallback placeholder score.
    assert 0 <= body["healthScore"] <= 100
    assert body["healthScore"] < 100  # must reflect real deductions, not a clean-slate default


@pytest.mark.asyncio
async def test_unusable_gemini_ingredient_objects_fall_back_to_raw_text(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Plain Water",
        "rawIngredientText": "Water, Salt",
        # The image prompt requires an ingredients array but does not define
        # its item schema. These objects are therefore valid Gemini output
        # but cannot be read as commonName/eNumber pairs.
        "ingredients": [{"name": "Water"}, {"name": "Salt"}],
        # Complete, trustworthy nutrition (V11: this test is specifically
        # about the ingredient-OBJECT-shape fallback, not about nutrition
        # completeness -- explicit real values here keep the two concerns
        # isolated so an incomplete-nutrition gate doesn't block this test).
        "sugarGrams": 0.0,
        "sodiumMg": 1.0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100_G",
        "novaGroup": 1,
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "unusable-ingredient-objects-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()

    returned_ids = [ingredient["id"] for ingredient in body["ingredients"]]
    assert returned_ids == ["synth_water", "synth_salt"]
    assert body["product"]["ingredientIds"] == ",".join(returned_ids)


# 2. Gemini returns invalid JSON -> fallback used --------------------------
#
# V11 (PR #9 review round 4, finding 2): the deterministic keyword-
# heuristic fallback's "raw text" is a hardcoded placeholder/error
# string, not real label content -- its nutrition is guessed and its
# "ingredients" are tokenized from that placeholder, so neither
# evidence group is genuinely verified. `/scan/label-image` (unlike
# `/scan/ocr-text`) now correctly refuses to return a confident-looking
# Health Score for this case, returning the SAME structured
# `labelScanRequired` response `/scan/barcode` already uses for an
# incomplete product, instead of a fabricated `200` -- see README
# section 11.9 and the module docstring's "Standalone label analysis"
# section for the full rationale and the Android-facing contract note.


@pytest.mark.asyncio
async def test_invalid_gemini_json_triggers_fallback(app_client, monkeypatch):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return "this is not valid JSON at all {{{"

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "invalid-json-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "PRODUCT_NOT_FOUND"
    assert error["details"]["labelScanRequired"] is True
    # The deterministic fallback still ran (proving the fallback chain
    # itself, unlike the fabricated-score behavior, is unchanged) --
    # its placeholder identity is still surfaced for the client to show.
    assert error["details"]["discoveredIdentity"]["productName"] == "Scanned Label Product"


# 3. Gemini network timeout -> fallback used -------------------------------

@pytest.mark.asyncio
async def test_gemini_network_timeout_triggers_fallback(app_client, monkeypatch):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated network timeout")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "timeout-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "PRODUCT_NOT_FOUND"
    assert error["details"]["labelScanRequired"] is True
    assert error["details"]["discoveredIdentity"]["productName"] == "Scanned Label Product"


# 4. Gemini API key missing -> fallback used -------------------------------

@pytest.mark.asyncio
async def test_missing_api_key_triggers_fallback(app_client):
    # No monkeypatch needed: the test environment runs with
    # GEMINI_API_KEY="" (see tests/conftest.py), so gemini_service.is_configured
    # is already False and GeminiService._call raises GeminiUnavailableError
    # internally, exactly like the "missing key" production scenario.
    assert gemini_service.is_configured is False

    headers = await _register_device(app_client, "missing-key-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "PRODUCT_NOT_FOUND"
    assert error["details"]["labelScanRequired"] is True
    assert error["details"]["discoveredIdentity"]["productName"] == "Scanned Label Product"


# 5. Both Gemini and fallback fail -> AI_SERVICE_UNAVAILABLE ----------------

@pytest.mark.asyncio
async def test_gemini_and_fallback_both_failing_returns_503(app_client, monkeypatch):
    import app.services.food_analysis as food_analysis_module

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated failure")

    def fake_fallback(*args, **kwargs):
        raise RuntimeError("simulated fallback failure")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    monkeypatch.setattr(food_analysis_module, "fallback_local_analysis", fake_fallback)

    headers = await _register_device(app_client, "double-failure-device")
    resp = await _upload(app_client, headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "AI_SERVICE_UNAVAILABLE"


# 6/7 already covered by the untouched, still-passing test suites:
# tests/integration/test_barcode_contract_change.py (barcode behavior)
# tests/integration/test_scan_flow.py (OCR text behavior)
