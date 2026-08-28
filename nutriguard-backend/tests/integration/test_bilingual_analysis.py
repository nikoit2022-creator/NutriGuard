"""
End-to-end regression tests for the bilingual-output requirement: every
name/text `POST /api/v1/scan/label-image` and `POST /api/v1/scan/ocr-text`
return to the Android client must be English or Bulgarian only.

Covers, through the real HTTP endpoints (not just the unit-level
parsers already covered in tests/unit/):
  - an English label
  - a Bulgarian label
  - a third-language label (must be translated, never leaked untranslated)
  - "null"/"None" placeholder values
  - the existing image and text fallback paths, unaffected by this change
"""
import io
import json

import pytest
from PIL import Image

from app.integrations.gemini import gemini_service


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


async def _register_device(client, device_id):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _upload_image(client, headers):
    files = {"image": ("label.jpg", _fake_jpeg_bytes(), "image/jpeg")}
    return client.post("/api/v1/scan/label-image", headers=headers, files=files)


def _force_gemini_configured(monkeypatch):
    """`gemini_service.is_configured` is a read-only property that is
    False in every test (GEMINI_API_KEY="" per conftest). Patch it at
    the class level to True so the OCR-text Gemini-success path
    (gemini_result_parser's documented quirk) can be exercised, exactly
    as it is used by end users with a real API key configured."""
    monkeypatch.setattr(type(gemini_service), "is_configured", property(lambda self: True))


# -- label-image: English label ----------------------------------------------

@pytest.mark.asyncio
async def test_label_image_english_label_names_pass_through(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Diet Cola",
        "brand": "Acme Beverages",
        "rawIngredientText": "Carbonated Water, Aspartame (E951)",
        "ingredients": [{"commonName": "Aspartame", "eNumber": "E951"}],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-en-image-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Diet Cola"
    assert body["product"]["brand"] == "Acme Beverages"


# -- label-image: Bulgarian label --------------------------------------------

@pytest.mark.asyncio
async def test_label_image_bulgarian_label_names_are_preserved(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Диетична Кола",
        "brand": "Acme Beverages",
        "rawIngredientText": "Газирана вода, Аспартам",
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-bg-image-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Диетична Кола"


# -- label-image: third-language label ---------------------------------------

@pytest.mark.asyncio
async def test_label_image_third_language_label_is_translated_not_leaked(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Zucker Drink",
        "rawIngredientText": "Wasser, Zucker",
        "ingredients": [{"commonName": "Zucker", "eNumber": None}],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-de-image-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()

    # Structured display fields are translated / never leak the original
    # third-language text. `rawIngredientText` is an intentionally
    # untouched verbatim OCR/Gemini transcript (existing, documented
    # behavior -- see README "Deviations"), not a "name" or "descriptive
    # value", so it is not subject to this guard.
    assert body["product"]["productName"] == "Sugar Drink"
    assert "Zucker" not in body["product"]["productName"]
    assert body["ingredients"][0]["commonName"] == "Sugar"
    assert "Zucker" not in body["ingredients"][0]["commonName"]


# -- label-image: "null"/"None" placeholders ---------------------------------

@pytest.mark.asyncio
async def test_label_image_null_string_brand_does_not_leak_as_the_brand(app_client, monkeypatch):
    gemini_payload = {
        "productName": "Real Product",
        "brand": "null",
        "rawIngredientText": "Water",
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-null-brand-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["brand"] == "Analyzed Brand"


@pytest.mark.asyncio
async def test_label_image_null_string_product_name_triggers_deterministic_fallback(
    app_client, monkeypatch
):
    gemini_payload = {"productName": "None", "rawIngredientText": "Water"}

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-null-name-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()
    # Same deterministic-fallback placeholder name used by every other
    # "Gemini gave nothing usable" case (see test_label_image_gemini_fix.py).
    assert body["product"]["productName"] == "Scanned Label Product"


# -- ocr-text: third-language product name via a successful Gemini call -----

@pytest.mark.asyncio
async def test_ocr_text_third_language_product_name_via_gemini_is_translated(
    app_client, monkeypatch
):
    _force_gemini_configured(monkeypatch)

    async def fake_analyze_text(raw_text: str) -> str:
        return json.dumps({"productName": "Zucker Drink", "brand": "Acme"})

    monkeypatch.setattr(gemini_service, "analyze_text", fake_analyze_text)

    headers = await _register_device(app_client, "bilingual-ocr-text-de-device")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, Sugar, Salt"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Sugar Drink"
    assert "Zucker" not in resp.text


# -- existing text fallback path: unaffected by this change ------------------

@pytest.mark.asyncio
async def test_ocr_text_deterministic_fallback_path_still_works(app_client):
    # GEMINI_API_KEY="" in the test environment (see conftest.py) ->
    # gemini_service.is_configured is False -> straight to the
    # deterministic fallback, exactly as before this change.
    headers = await _register_device(app_client, "bilingual-ocr-text-fallback-device")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, Sugar, Salt, Sodium Benzoate (E211)"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Scanned Product"
    assert body["product"]["sugarGrams"] == 14.0


# -- existing image fallback path: unaffected by this change -----------------

@pytest.mark.asyncio
async def test_label_image_deterministic_fallback_path_still_works(app_client, monkeypatch):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return "this is not valid JSON at all {{{"

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(app_client, "bilingual-image-fallback-device")
    resp = await _upload_image(app_client, headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Scanned Label Product"
