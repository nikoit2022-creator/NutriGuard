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
import uuid

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.base import Base
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


# --- 17. Transaction atomicity (review finding 3): rollback proves no ------
# --- partial writes; incomplete enrichment still commits atomically -------


@pytest_asyncio.fixture
async def strict_db_session():
    """
    A dedicated, ISOLATED in-memory SQLite engine/session -- deliberately
    NOT the shared `db_engine`/`db_session` every other test in this
    suite uses -- with the standard SQLAlchemy pysqlite/aiosqlite
    transactional-quirk workaround applied (see SQLAlchemy's dialect
    docs, "Serializable isolation / Savepoints / Transactional DDL"):
    without it, the sqlite3 DBAPI's own legacy implicit-transaction
    tracking can silently make a SAVEPOINT (`Session.begin_nested()` --
    used by `product_repository.insert_new` / `product_source_repository.
    record_discovery` for race-safe writes) behave like a real commit
    that survives a later `session.rollback()` -- never a problem
    against the app's real PostgreSQL deployment, but it would make the
    rollback tests below pass or fail based on a SQLite driver artifact
    rather than the actual transaction-boundary guarantee being tested.

    Kept as a separate engine (not applied to the shared `db_engine`
    fixture) deliberately: applying the stricter, correct transaction
    semantics suite-wide surfaces unrelated pre-existing assumptions in
    OTHER tests (built around the shared engine's single physical
    connection, see `product_repository.insert_new`'s own docstring) --
    out of scope for this fix; this fixture gives the rollback tests a
    real guarantee without touching any other test's behavior.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _disable_pysqlite_implicit_transactions(dbapi_connection, connection_record):
        dbapi_connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def _emit_explicit_begin(conn):
        conn.exec_driver_sql("BEGIN")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_provenance_write_failure_rolls_back_the_whole_enrichment(monkeypatch, strict_db_session):
    """A failure recording provenance must undo the product insert from
    earlier in the SAME call -- never a product row left behind with no
    provenance at all. A plain `session.rollback()` (exactly what
    `app.database.session.get_db` does on any exception in production)
    after the failure must be enough to leave zero trace -- proving
    nothing was committed prematurely."""
    import app.services.food_analysis as food_analysis_module
    from app.services import food_analysis

    _mock_full_gemini(monkeypatch)

    async def fake_record_discovery(*args, **kwargs):
        raise RuntimeError("simulated provenance write failure")

    monkeypatch.setattr(food_analysis_module.product_source_repository, "record_discovery", fake_record_discovery)

    # No real `users` row is needed: this failure happens before
    # `scan_history` (the only FK on `user_id`) is ever touched.
    user_id = uuid.uuid4()
    barcode = "6639233214683"
    with pytest.raises(RuntimeError, match="simulated provenance write failure"):
        await food_analysis.analyze_label_image_with_barcode(
            strict_db_session, user_id, _fake_jpeg_bytes(), barcode
        )
    await strict_db_session.rollback()

    assert await _product_count(strict_db_session, barcode) == 0
    sources = (
        await strict_db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    assert sources == []


@pytest.mark.asyncio
async def test_scan_history_write_failure_rolls_back_product_and_provenance_too(monkeypatch, strict_db_session):
    """Proves the successful-enrichment path is genuinely ONE
    transaction, not "commit product+provenance early, then separately
    fail on history": a failure this late must still undo the product
    insert AND the provenance write from earlier in the same call, once
    the caller rolls back exactly as `get_db` does in production."""
    import app.services.food_analysis as food_analysis_module
    from app.services import food_analysis

    _mock_full_gemini(monkeypatch)

    async def fake_insert(*args, **kwargs):
        raise RuntimeError("simulated scan-history write failure")

    # The fake raises before ever touching the DB, so no real `users`
    # row is needed to satisfy `scan_history`'s FK either.
    monkeypatch.setattr(food_analysis_module.scan_history_repository, "insert", fake_insert)

    user_id = uuid.uuid4()
    barcode = "8197369935639"
    with pytest.raises(RuntimeError, match="simulated scan-history write failure"):
        await food_analysis.analyze_label_image_with_barcode(
            strict_db_session, user_id, _fake_jpeg_bytes(), barcode
        )
    await strict_db_session.rollback()

    assert await _product_count(strict_db_session, barcode) == 0
    sources = (
        await strict_db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    assert sources == []


@pytest.mark.asyncio
async def test_incomplete_enrichment_still_commits_product_and_provenance_together(app_client, monkeypatch, db_session):
    """The one legitimate case that DOES commit before returning a
    non-200 response: an incomplete (`labelScanRequired`) enrichment.
    The product row and its provenance land together -- never one
    without the other."""
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated failure -- forces the deterministic fallback")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    headers = await _register_device(app_client, "incomplete-provenance-device")

    barcode = "4806358178394"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    assert await _product_count(db_session, barcode) == 1
    sources = (
        await db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    assert len(sources) == 1
    assert sources[0].provider == "label_ocr"


# --- 18. Review finding 7: provenance confidence and verification timestamp


@pytest.mark.asyncio
async def test_incomplete_enrichment_does_not_stamp_last_verified_at(app_client, monkeypatch, db_session):
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated failure -- forces the deterministic fallback")

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)
    headers = await _register_device(app_client, "no-misleading-timestamp-device")

    barcode = "0416410688552"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored is not None
    assert stored.has_verified_nutrition is False
    assert stored.last_verified_at is None


@pytest.mark.asyncio
async def test_verified_enrichment_does_stamp_last_verified_at(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "verified-timestamp-device")

    barcode = "0225853206199"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is True
    assert stored.last_verified_at is not None


@pytest.mark.asyncio
async def test_ocr_provenance_confidence_is_not_fabricated(app_client, monkeypatch, db_session):
    """The OCR-extraction pipeline never reports its own confidence --
    the provenance row must use the schema's conservative default
    (`0.0`), never a fabricated `1.0` "full confidence" claim."""
    _mock_full_gemini(monkeypatch)
    headers = await _register_device(app_client, "provenance-confidence-device")

    barcode = "6105710122043"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200

    sources = (
        await db_session.execute(
            select(ProductSource).where(ProductSource.barcode == barcode, ProductSource.provider == "label_ocr")
        )
    ).scalars().all()
    assert len(sources) == 1
    assert float(sources[0].confidence) == 0.0


# --- 19. Rejected nutrition/NOVA/allergens must never be persisted --------
# --- (review round 3, findings 1 and 3) -------------------------------------


@pytest.mark.asyncio
async def test_nan_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, sodiumMg=float("nan"))
    headers = await _register_device(app_client, "nan-nutrition-device")

    barcode = "3775945807980"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404  # sodiumMg rejected -> still incomplete

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored is not None
    assert stored.has_verified_nutrition is False
    assert float(stored.sodium_mg) == 0.0  # safe neutral, never NaN


@pytest.mark.asyncio
async def test_infinity_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, saturatedFatGrams=float("inf"))
    headers = await _register_device(app_client, "infinity-nutrition-device")

    barcode = "6941115109544"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is False
    assert float(stored.saturated_fat_grams) == 0.0


@pytest.mark.asyncio
async def test_negative_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, sugarGrams=-50.0)
    headers = await _register_device(app_client, "negative-nutrition-device")

    barcode = "9413482690590"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is False
    assert float(stored.sugar_grams) == 0.0


@pytest.mark.asyncio
async def test_boolean_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, sugarGrams=True)
    headers = await _register_device(app_client, "boolean-nutrition-device")

    barcode = "2981884718725"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is False
    assert float(stored.sugar_grams) == 0.0  # never 1.0 (float(True))


@pytest.mark.asyncio
async def test_out_of_range_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, sodiumMg=5_000_000.0)
    headers = await _register_device(app_client, "out-of-range-nutrition-device")

    barcode = "0743961627182"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is False
    assert float(stored.sodium_mg) == 0.0


@pytest.mark.asyncio
async def test_placeholder_string_nutrition_value_is_never_persisted_db_level(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, saturatedFatGrams="null")
    headers = await _register_device(app_client, "placeholder-nutrition-device")

    barcode = "6854312189007"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.has_verified_nutrition is False
    assert float(stored.saturated_fat_grams) == 0.0


@pytest.mark.asyncio
async def test_incomplete_enrichment_does_not_overwrite_existing_safe_nutrition_with_rejected_value(
    app_client, monkeypatch, db_session
):
    """A field this attempt couldn't trust must leave the existing
    value untouched -- never blanked to the safe-neutral placeholder,
    and never overwritten with rejected model output."""
    barcode = "7944021761768"
    incomplete = Product(
        barcode=barcode,
        product_name="Partially Known Product",
        brand="Some Brand",
        category="",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=5.5,  # already known from an earlier attempt
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="",
        source="label_scan",
        is_verified=False,
        has_verified_nutrition=False,
    )
    db_session.add(incomplete)
    await db_session.commit()

    # This attempt's sugarGrams is REJECTED (NaN); sodium/saturatedFat are valid.
    _mock_full_gemini(monkeypatch, sugarGrams=float("nan"), sodiumMg=77.0, saturatedFatGrams=2.5)
    headers = await _register_device(app_client, "preserve-existing-nutrition-device")

    resp = await _upload(app_client, headers, barcode=barcode)
    # sugarGrams rejected this round -> still incomplete overall.
    assert resp.status_code == 404

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert float(stored.sugar_grams) == 5.5  # PRESERVED, not overwritten with 0.0
    assert float(stored.sodium_mg) == 77.0  # filled in from this attempt
    assert float(stored.saturated_fat_grams) == 2.5  # filled in from this attempt
    assert stored.has_verified_nutrition is False


# --- NOVA group -------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_nova_group_stored_as_safe_unknown_sentinel(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, novaGroup=999)
    headers = await _register_device(app_client, "invalid-nova-device")

    barcode = "6675985898092"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    assert resp.json()["product"]["novaGroup"] == 0

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.nova_group == 0


@pytest.mark.asyncio
async def test_boolean_nova_group_is_rejected(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, novaGroup=True)
    headers = await _register_device(app_client, "boolean-nova-device")

    barcode = "0549862476047"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    assert resp.json()["product"]["novaGroup"] == 0


@pytest.mark.asyncio
async def test_valid_nova_group_persists_and_zero_deduction_never_fabricated_for_invalid(
    app_client, monkeypatch, db_session
):
    """A real NOVA 4 classification takes its documented 20-point
    deduction; an invalid classification (rejected to the safe-unknown
    sentinel) must fall back to the health-score calculator's own
    zero-deduction branch, never silently inheriting a real deduction."""
    barcode_valid = "7587225144465"
    _mock_full_gemini(monkeypatch, novaGroup=4)
    headers = await _register_device(app_client, "nova-score-device")
    resp_valid = await _upload(app_client, headers, barcode=barcode_valid)
    assert resp_valid.status_code == 200
    assert resp_valid.json()["product"]["novaGroup"] == 4

    barcode_invalid = "2897921715162"
    _mock_full_gemini(monkeypatch, novaGroup=-7)
    resp_invalid = await _upload(app_client, headers, barcode=barcode_invalid)
    assert resp_invalid.status_code == 200
    assert resp_invalid.json()["product"]["novaGroup"] == 0
    # Same underlying analysis otherwise -- the invalid-NOVA product's
    # score must be HIGHER (or equal) than the valid-NOVA-4 product's,
    # since NOVA 4 carries a real 20-point deduction and the unknown
    # sentinel carries none.
    assert resp_invalid.json()["healthScore"] >= resp_valid.json()["healthScore"]


# --- Allergens ----------------------------------------------------------


@pytest.mark.asyncio
async def test_allergens_explicitly_extracted_are_persisted(app_client, monkeypatch, db_session):
    _mock_full_gemini(monkeypatch, allergens=["Milk", "Soy"])
    headers = await _register_device(app_client, "allergens-extracted-device")

    barcode = "9392194796861"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    assert resp.json()["product"]["allergensDetected"] == "Milk, Soy"


@pytest.mark.asyncio
async def test_allergens_unknown_is_never_a_confirmed_absence_claim(app_client, monkeypatch, db_session):
    """No "allergens" key at all in the model response -- must persist
    as unknown/empty, never the string "None" (a fabricated confirmed-
    absence claim)."""
    _mock_full_gemini(monkeypatch)  # _full_gemini_payload has no "allergens" key
    headers = await _register_device(app_client, "allergens-unknown-device")

    barcode = "0035226754062"
    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    assert resp.json()["product"]["allergensDetected"] == ""
    assert resp.json()["product"]["allergensDetected"] != "None"

    db_session.expire_all()
    stored = await db_session.get(Product, barcode)
    assert stored.allergens_detected == ""


@pytest.mark.asyncio
async def test_existing_allergens_not_overwritten_by_unknown_new_payload(app_client, monkeypatch, db_session):
    barcode = "7415048029112"
    incomplete = Product(
        barcode=barcode,
        product_name="Known Allergen Product",
        brand="Some Brand",
        category="",
        raw_ingredient_text="",
        ingredient_ids="",
        health_score=0,
        nova_group=0,
        sugar_grams=0,
        sodium_mg=0,
        saturated_fat_grams=0,
        allergens_detected="Peanuts",  # already known from an earlier attempt
        source="label_scan",
        is_verified=False,
        has_verified_nutrition=False,
    )
    db_session.add(incomplete)
    await db_session.commit()

    _mock_full_gemini(monkeypatch)  # no "allergens" key this time -> unknown
    headers = await _register_device(app_client, "preserve-allergens-device")

    resp = await _upload(app_client, headers, barcode=barcode)
    assert resp.status_code == 200
    assert resp.json()["product"]["allergensDetected"] == "Peanuts"  # preserved, not blanked


# --- Warning pipeline sanity (finding 3) ------------------------------------


@pytest.mark.asyncio
async def test_warning_pipeline_unaffected_by_unknown_allergens_and_invalid_nova(app_client, monkeypatch):
    """Neither `allergens_detected` nor `nova_group` are consumed by the
    Personalized Warning Engine (it uses the boolean dietary flags, and
    the Health Score Calculator, respectively) -- confirms the endpoint
    still returns a well-formed, non-crashing warnings list when both
    are unknown/invalid simultaneously."""
    _mock_full_gemini(monkeypatch, novaGroup="not-an-int")
    headers = await _register_device(app_client, "warning-pipeline-sanity-device")

    resp = await _upload(app_client, headers, barcode="1522904214455")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["warnings"], list)
    assert body["product"]["allergensDetected"] == ""
    assert body["product"]["novaGroup"] == 0
