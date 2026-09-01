"""
End-to-end tests for barcode + label enrichment: `POST
/api/v1/scan/label-image` (and `/scan/ocr-text`) extended with an
optional `barcode` field that combines barcode identity with label
analysis into one canonical, persisted product (see README "Barcode +
label enrichment").

Every Gemini call is mocked (`gemini_service.analyze_image` /
`.translate_label_text`) -- no test in this file makes a live network
call, matching the rest of the suite's conventions (see
`tests/conftest.py`).
"""
import io
import json

import pytest
from PIL import Image
from sqlalchemy import func, select

from app.integrations.gemini import GeminiUnavailableError, gemini_service
from app.models.product import Product
from app.models.product_source import ProductSource

# Checksum-valid EAN-13 barcodes (verified against
# `barcode_validation.validate_and_normalize`) not present in the
# seeded database, each used by exactly one test to avoid cross-test
# row collisions.
UNKNOWN_BARCODE = "1043321819608"
UNKNOWN_BARCODE_2 = "0133890838634"
UNKNOWN_BARCODE_3 = "7940265423516"
UNKNOWN_BARCODE_4 = "1615594078169"
UNKNOWN_BARCODE_5 = "1849593103410"
UNKNOWN_BARCODE_6 = "3164752553416"
UNKNOWN_BARCODE_7 = "9283276483505"
# Its 12-digit UPC-A form and 13-digit EAN-13-zero-padded equivalent.
UPC_A_BARCODE = "036000291452"
UPC_A_AS_EAN13 = "0036000291452"


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
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
    payload = {
        "productName": "Bolt Energy Drink",
        "brand": "Bolt Beverages",
        "sugarGrams": 12.0,
        "sodiumMg": 40.0,
        "saturatedFatGrams": 0.0,
        "hasArtificialSweeteners": False,
        "hasPreservatives": True,
        "isGlutenFree": True,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        "rawIngredientText": "Carbonated Water, Sugar, Citric Acid, Sodium Benzoate (E211)",
        "ingredients": [
            {"commonName": "Sugar"},
            {"commonName": "Citric Acid"},
            {"commonName": "Sodium Benzoate", "eNumber": "E211"},
        ],
    }
    payload.update(overrides)
    return payload


def _mock_full_gemini(monkeypatch, **overrides):
    payload = _full_gemini_payload(**overrides)

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    return payload


async def _product_count(db_session, barcode: str) -> int:
    result = await db_session.execute(
        select(func.count()).select_from(Product).where(Product.barcode == barcode)
    )
    return result.scalar_one()


# --- 1. Backward compatibility (no barcode) --------------------------------


@pytest.mark.asyncio
async def test_label_image_without_barcode_is_backward_compatible(app_client, monkeypatch):
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "no-barcode-device")

    resp = await _upload(app_client, headers)  # no `barcode` field at all
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["barcode"].startswith("img_")
    assert body["product"]["productName"] == "Bolt Energy Drink"
    assert body["isFromDatabaseCache"] is False


@pytest.mark.asyncio
async def test_label_image_with_blank_barcode_field_is_backward_compatible(app_client, monkeypatch):
    """A client sending the multipart field but empty (or a literal
    placeholder) must be treated identically to not sending it at all."""
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "blank-barcode-device")

    resp = await _upload(app_client, headers, barcode="null")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["barcode"].startswith("img_")


# --- 2. Unknown barcode + label scan creates one canonical product --------


@pytest.mark.asyncio
async def test_unknown_barcode_plus_label_scan_creates_one_canonical_product(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "new-barcode-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["barcode"] == UNKNOWN_BARCODE
    assert body["product"]["productName"] == "Bolt Energy Drink"
    assert body["isFromDatabaseCache"] is False

    assert await _product_count(db_session, UNKNOWN_BARCODE) == 1


# --- 3. Incomplete discovered product + label scan enriches the same row --


@pytest.mark.asyncio
async def test_incomplete_discovered_product_is_enriched_in_place(app_client, monkeypatch, db_session):
    incomplete = Product(
        barcode=UNKNOWN_BARCODE_2,
        product_name="Some Discovered Identity",
        brand="",
        category="",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="open_food_facts",
        source_confidence=0.75,
        is_verified=False,
        has_verified_nutrition=False,
    )
    db_session.add(incomplete)
    await db_session.commit()

    _mock_full_gemini(monkeypatch)  # brand="Bolt Beverages" -- fills the existing placeholder ""
    headers = await _register_device(app_client, "enrich-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_2)
    assert resp.status_code == 200
    body = resp.json()

    # Identity that was already meaningful is PRESERVED, not overwritten.
    assert body["product"]["productName"] == "Some Discovered Identity"
    # Nutrition, which was NOT verified, is filled in from the label scan.
    assert body["product"]["sugarGrams"] == 12.0

    assert await _product_count(db_session, UNKNOWN_BARCODE_2) == 1
    # `db_session` is a separate Session from the one the request used
    # (see `app_client`/`db_session` fixtures) -- expire its identity
    # map so `.get()` re-reads the row the request just committed
    # instead of returning this session's own stale cached instance.
    db_session.expire_all()
    refreshed = await db_session.get(Product, UNKNOWN_BARCODE_2)
    assert refreshed.has_verified_nutrition is True
    assert refreshed.product_name == "Some Discovered Identity"  # meaningful existing value preserved
    assert refreshed.brand == "Bolt Beverages"  # placeholder "" was filled from OCR


# --- 4. Subsequent barcode lookup returns the enriched local product ------


@pytest.mark.asyncio
async def test_subsequent_barcode_scan_returns_enriched_product_without_discovery(app_client, monkeypatch):
    from app.services import barcode_discovery

    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "enrich-then-scan-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_3)
    assert resp.status_code == 200

    async def _must_not_be_called(*args, **kwargs):
        raise AssertionError("discover_product must not run for a locally-stored product")

    monkeypatch.setattr(barcode_discovery, "discover_product", _must_not_be_called)

    resp2 = await app_client.post("/api/v1/scan/barcode", json={"barcode": UNKNOWN_BARCODE_3}, headers=headers)
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["isFromDatabaseCache"] is True
    assert body2["product"]["productName"] == "Bolt Energy Drink"


# --- 5. UPC-A/EAN-13 alias enrichment never duplicates ---------------------


@pytest.mark.asyncio
async def test_upc_a_ean13_alias_enrichment_does_not_duplicate(app_client, monkeypatch, db_session):
    legacy = Product(
        barcode=UPC_A_BARCODE,  # stored under the legacy 12-digit UPC-A form
        product_name="",
        brand="",
        category="",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="None",
        source="upcitemdb",
        is_verified=False,
        has_verified_nutrition=False,
    )
    db_session.add(legacy)
    await db_session.commit()

    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "alias-enrich-device")

    # Scan using the 13-digit EAN-13-zero-padded equivalent of the same barcode.
    resp = await _upload(app_client, headers, barcode=UPC_A_AS_EAN13)
    assert resp.status_code == 200
    body = resp.json()
    # Enrichment happened on the ORIGINAL (12-digit) row, not a new 13-digit one.
    assert body["product"]["barcode"] == UPC_A_BARCODE

    assert await _product_count(db_session, UPC_A_BARCODE) == 1
    assert await _product_count(db_session, UPC_A_AS_EAN13) == 0


# --- 6. Repeated enrichment is idempotent -----------------------------------


@pytest.mark.asyncio
async def test_repeated_enrichment_of_same_barcode_is_idempotent(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "idempotent-device")

    resp1 = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_4)
    assert resp1.status_code == 200
    assert resp1.json()["isFromDatabaseCache"] is False

    resp2 = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_4)
    assert resp2.status_code == 200
    assert resp2.json()["isFromDatabaseCache"] is True
    assert resp2.json()["product"]["productName"] == resp1.json()["product"]["productName"]

    assert await _product_count(db_session, UNKNOWN_BARCODE_4) == 1


# --- 7. Verified data is never overwritten by lower-confidence OCR --------


@pytest.mark.asyncio
async def test_verified_local_data_is_not_overwritten_by_ocr(app_client, monkeypatch, db_session):
    verified = Product(
        barcode=UNKNOWN_BARCODE_5,
        product_name="Trusted Verified Product",
        brand="TrustedBrand",
        category="Beverages",
        raw_ingredient_text="Water, Real Sugar",
        ingredient_ids="",
        health_score=90,
        nova_group=1,
        sugar_grams=5.5,
        sodium_mg=10.0,
        saturated_fat_grams=0.1,
        allergens_detected="None",
        source="label_scan",
        is_verified=True,
        has_verified_nutrition=True,
    )
    db_session.add(verified)
    await db_session.commit()

    _mock_full_gemini(monkeypatch, sugarGrams=99.0, brand="Attempted Overwrite Brand")
    headers = await _register_device(app_client, "protect-verified-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_5)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == "Trusted Verified Product"
    assert body["product"]["brand"] == "TrustedBrand"
    assert body["product"]["sugarGrams"] == 5.5
    assert body["isFromDatabaseCache"] is True

    assert await _product_count(db_session, UNKNOWN_BARCODE_5) == 1


# --- 8. Incomplete nutrition stays labelScanRequired/unverified -----------


@pytest.mark.asyncio
async def test_incomplete_label_scan_stays_label_scan_required(app_client, monkeypatch, db_session):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated failure -- forces the deterministic fallback")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    headers = await _register_device(app_client, "incomplete-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_6)
    assert resp.status_code == 404
    error = resp.json()["error"]
    assert error["code"] == "PRODUCT_NOT_FOUND"
    assert error["details"]["labelScanRequired"] is True

    # The identity was still persisted (so a later scan doesn't re-do
    # provider/label work from scratch), but flagged unverified.
    assert await _product_count(db_session, UNKNOWN_BARCODE_6) == 1
    db_session.expire_all()
    stored = await db_session.get(Product, UNKNOWN_BARCODE_6)
    assert stored.has_verified_nutrition is False
    assert stored.is_verified is False


# --- 9. Invalid barcode -> standard structured validation error -----------


@pytest.mark.asyncio
async def test_invalid_barcode_returns_structured_validation_error(app_client, db_session):
    headers = await _register_device(app_client, "invalid-barcode-device")

    resp = await _upload(app_client, headers, barcode="not-a-real-barcode")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    assert await _product_count(db_session, "not-a-real-barcode") == 0


@pytest.mark.asyncio
async def test_invalid_barcode_checksum_returns_structured_validation_error(app_client):
    headers = await _register_device(app_client, "bad-checksum-device")
    # Correct length (13 digits), wrong GS1 check digit.
    resp = await _upload(app_client, headers, barcode="4006381333930")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# --- 10. Mixed English/Bulgarian content is deduplicated, end-to-end ------


@pytest.mark.asyncio
async def test_mixed_english_bulgarian_content_deduplicated_end_to_end(app_client, monkeypatch, db_session):
    _mock_full_gemini(
        monkeypatch,
        rawIngredientText="Water, Sugar, Salt / Вода, Захар, Сол",
        ingredients=[],  # force fallback to raw-text tokenization for a clean assertion
    )
    headers = await _register_device(app_client, "bilingual-device")

    resp = await _upload(app_client, headers, barcode=UNKNOWN_BARCODE_7)
    assert resp.status_code == 200
    body = resp.json()

    raw_text = body["product"]["rawIngredientText"]
    assert "Water" in raw_text and "Захар" in raw_text  # both languages retained

    # 6 raw tokens (3 EN + 3 BG) collapse to 3 unique ingredients.
    assert len(body["ingredients"]) == 3


# --- 11. Other-language-only label is translated to canonical English -----


@pytest.mark.asyncio
async def test_other_language_only_label_is_translated_end_to_end(app_client, monkeypatch, db_session):
    _mock_full_gemini(
        monkeypatch,
        rawIngredientText="Wasser, Zucker, Salz, Natriumbenzoat (E211)",
        ingredients=[],
    )

    async def fake_translate(text: str) -> str:
        return json.dumps(
            {
                "detectedLanguage": "de",
                "confidence": 0.9,
                "translatedText": "Water, Sugar, Salt, Sodium Benzoate (E211)",
            }
        )

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)
    headers = await _register_device(app_client, "translate-device")

    barcode = "7501031311309"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    body = resp.json()
    assert "Water" in body["product"]["rawIngredientText"]
    assert "Wasser" not in body["product"]["rawIngredientText"]

    sources = (
        await db_session.execute(select(ProductSource).where(ProductSource.provider == "label_translation"))
    ).scalars().all()
    matching = [s for s in sources if s.barcode == barcode]
    assert len(matching) == 1
    assert matching[0].language == "en"


# --- 12. Translation failure/low confidence: controlled error, nothing persisted invalid ---


@pytest.mark.asyncio
async def test_translation_failure_returns_controlled_error_and_persists_nothing(app_client, monkeypatch, db_session):
    _mock_full_gemini(
        monkeypatch,
        rawIngredientText="Wasser, Zucker, Salz, Natriumbenzoat (E211)",
        ingredients=[],
    )

    async def fake_translate(text: str) -> str:
        return json.dumps({"detectedLanguage": "de", "confidence": 0.05, "translatedText": "??"})

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)
    headers = await _register_device(app_client, "translate-fail-device")

    barcode = "7242388496966"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "LABEL_TRANSLATION_UNRELIABLE"

    assert await _product_count(db_session, barcode) == 0


# --- 13. No raw image is stored ---------------------------------------------


@pytest.mark.asyncio
async def test_no_raw_image_bytes_are_persisted(app_client, monkeypatch, db_session):
    image_bytes = _fake_jpeg_bytes()
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "no-image-storage-device")

    files = {"image": ("label.jpg", image_bytes, "image/jpeg")}
    resp = await app_client.post(
        "/api/v1/scan/label-image", headers=headers, files=files, data={"barcode": "5328710122696"}
    )
    assert resp.status_code == 200

    db_session.expire_all()
    stored = await db_session.get(Product, "5328710122696")
    for column in Product.__table__.columns:
        value = getattr(stored, column.name)
        if isinstance(value, (bytes, bytearray)):
            assert image_bytes not in bytes(value)
    for column in Product.__table__.columns:
        value = getattr(stored, column.name)
        if isinstance(value, str):
            assert image_bytes.decode("latin-1") not in value


# --- 14. Placeholder normalization -----------------------------------------


@pytest.mark.asyncio
async def test_literal_placeholder_barcode_values_become_none(app_client, monkeypatch):
    """"null"/"N/A"/whitespace supplied for the optional `barcode` field
    must normalize to "no barcode supplied", never be treated as a
    literal (invalid) barcode string."""
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "placeholder-barcode-device")

    for placeholder in ("null", "N/A", "  ", "None"):
        resp = await _upload(app_client, headers, barcode=placeholder)
        assert resp.status_code == 200, placeholder
        assert resp.json()["product"]["barcode"].startswith("img_")


# --- 15. OCR-text endpoint: backward compatible + barcode enrichment ------


@pytest.mark.asyncio
async def test_ocr_text_without_barcode_is_backward_compatible(app_client):
    headers = await _register_device(app_client, "ocr-text-no-barcode-device")
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, Sugar, Salt, Citric Acid"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["product"]["barcode"].startswith("ocr_")


@pytest.mark.asyncio
async def test_ocr_text_with_barcode_enriches_canonical_product(app_client, db_session):
    headers = await _register_device(app_client, "ocr-text-barcode-device")
    barcode = "5146270482810"
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, Sugar, Salt, Citric Acid", "barcode": barcode},
        headers=headers,
    )
    # Deterministic fallback nutrition (the documented OCR-text quirk --
    # see food_analysis.analyze_ocr_text_with_barcode) is never treated
    # as genuinely verified, so this stays labelScanRequired.
    assert resp.status_code == 404
    assert resp.json()["error"]["details"]["labelScanRequired"] is True
    assert await _product_count(db_session, barcode) == 1


# --- 16. WHO/IARC classification: real JSON null, never fabricated --------


@pytest.mark.asyncio
async def test_who_iarc_classification_is_real_json_null_for_synthetic_ingredients(app_client, monkeypatch):
    _mock_full_gemini(
        monkeypatch,
        rawIngredientText="Contains: Some Unmatched Novel Ingredient With Water",
        ingredients=[{"commonName": "Some Unmatched Novel Ingredient"}],
    )
    headers = await _register_device(app_client, "iarc-null-device")

    resp = await _upload(app_client, headers, barcode="1669784801846")
    assert resp.status_code == 200
    body = resp.json()
    ingredient = body["ingredients"][0]
    assert "whoIarcClassification" in ingredient
    assert ingredient["whoIarcClassification"] is None
    # The raw response body must contain a real `null`, never the string "null".
    assert '"whoIarcClassification":"null"' not in resp.text.replace(" ", "")
