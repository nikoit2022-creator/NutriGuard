"""
Issue #25 (owner-approved continuation, 2026-09-26) -- non-empty label text
that yields no usable ingredient token (e.g. "---", "...", "; ; ,") is a
`RESOLUTION_FAILED` failure, never a falsely complete ingredient group.

Defect reproduced at bc709dd (before this change): on all four paths
(`/scan/label-image` and `/scan/ocr-text`, standalone and barcode-linked)
such text returned `200` with `ingredients: []` and
`hasVerifiedIngredients: true`; barcode-linked it also counted as a
complete group and could replace a verified ingredient list.

"Usable" is an identity question, not a curation one: a valid but unknown
ingredient name is still returned, with no invented description or rating.
Every assertion is on the actual HTTP JSON; every Gemini call is mocked.
"""
import pytest
from sqlalchemy import func, select

from app.models.product import Product
from app.models.product_source import ProductSource
from app.models.scan_history import ScanHistory
from tests.integration.test_ingredient_first_label_scan import (
    ENGLISH_LABEL,
    FULL_NUTRITION,
    IDENTITY_FOUND_REASON_PREFIX,
    SECRET,
    _capture,
    _headers,
    _names,
    _scan,
)

UNRESOLVABLE = ["---", "...", "; ; ,", "1234, 5678"]
# Checksum-valid EAN-13s not used by any other test file.
BARCODE_A = "5449000000996"
BARCODE_B = "4014400900057"
BARCODE_C = "8410000000016"
BARCODE_D = "3017620422003"
BARCODE_E = "5000112637922"


async def _post(app_client, monkeypatch, endpoint: str, text: str, device: str, *, barcode: str | None = None,
                extra: dict | None = None):
    """One label/OCR scan of `text` through the real endpoint."""
    if endpoint == "label-image":
        return await _scan(
            app_client, monkeypatch, {"rawIngredientText": text, **(extra or {})}, device, barcode=barcode
        )
    body = {"rawText": text}
    if barcode:
        body["barcode"] = barcode
    return await app_client.post(
        "/api/v1/scan/ocr-text", headers=await _headers(app_client, device), json=body
    )


def _assert_resolution_failed(resp) -> dict:
    assert resp.status_code == 404, resp.text
    error = resp.json()["error"]
    assert error["code"] == "PRODUCT_NOT_FOUND"
    details = error["details"]
    assert details["failureReason"] == "RESOLUTION_FAILED"
    assert details["labelScanRequired"] is True
    assert details["analysisComplete"] is False
    assert details["healthScore"] is None
    assert SECRET not in resp.text
    # Honest text: never claims an identity was found, never guesses photo quality.
    assert not details["reason"].startswith(IDENTITY_FOUND_REASON_PREFIX)
    for guess in ("blur", "focus", "lit", "light"):
        assert guess not in details["reason"].lower()
    return details


async def _product(db_session, barcode: str) -> Product:
    return (await db_session.execute(select(Product).where(Product.barcode == barcode))).scalar_one()


async def _count(db_session, model, *where) -> int:
    return (await db_session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# --- 1. Standalone: image and OCR --------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
@pytest.mark.parametrize("text", UNRESOLVABLE)
async def test_standalone_unresolvable_text_is_resolution_failed(app_client, monkeypatch, db_session, endpoint, text):
    resp = await _post(app_client, monkeypatch, endpoint, text, f"rf-standalone-{endpoint}-{abs(hash(text))}")
    details = _assert_resolution_failed(resp)

    assert "ingredients" not in details  # nothing fabricated
    assert details["ingredientsScanRequired"] is True
    barcode = details["discoveredIdentity"]["barcode"]
    assert barcode.startswith("img_" if endpoint == "label-image" else "ocr_")  # never a fake real barcode
    stored = await _product(db_session, barcode)
    assert stored.has_verified_ingredients is False  # no falsely complete group
    assert stored.is_verified is False
    assert await _count(db_session, ScanHistory) == 0  # no history entry for a failed scan


@pytest.mark.asyncio
async def test_label_image_single_character_text_is_resolution_failed(app_client, monkeypatch):
    # One-character tokens are dropped by tokenization, leaving nothing.
    _assert_resolution_failed(await _post(app_client, monkeypatch, "label-image", "x", "rf-one-char"))


@pytest.mark.asyncio
async def test_unresolvable_text_is_never_echoed_or_stored_as_ingredients(app_client, monkeypatch):
    resp = await _post(app_client, monkeypatch, "ocr-text", "90210, 80808", "rf-no-echo")
    _assert_resolution_failed(resp)
    assert "90210" not in resp.text and "80808" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
async def test_resolution_failure_envelope_has_the_same_keys_as_an_empty_extraction(app_client, monkeypatch, endpoint):
    """Old-client compatibility: same status, code and detail keys as the
    existing empty-extraction 404; only the additive value differs."""
    empty = await _scan(app_client, monkeypatch, {"rawIngredientText": ""}, "rf-compat-empty")
    resp = await _post(app_client, monkeypatch, endpoint, "---", f"rf-compat-{endpoint}")
    assert resp.status_code == empty.status_code == 404
    assert set(resp.json()["error"]["details"]) == set(empty.json()["error"]["details"])
    assert resp.json()["error"]["details"]["failureReason"] != empty.json()["error"]["details"]["failureReason"]


# --- 2. Mixed valid/invalid tokens: valid ones are kept -----------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
@pytest.mark.parametrize("text", ["Sugar; ---; Salt", "---, Sugar, ..., Salt, ; ;"])
async def test_mixed_valid_and_invalid_tokens_keep_the_valid_ones(app_client, monkeypatch, endpoint, text):
    resp = await _post(app_client, monkeypatch, endpoint, text, f"rf-mixed-{endpoint}-{abs(hash(text))}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _names(body) == ["Sugar", "Salt"]
    assert body["product"]["hasVerifiedIngredients"] is True
    assert "failureReason" not in resp.text
    for ing in body["ingredients"]:
        assert ing["description"] == ""  # unknown descriptions stay empty
        assert ing["riskAssessmentAvailable"] is False  # no invented rating
        assert ing["verificationStatus"] == "UNVERIFIED"  # usable != scientifically verified


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
@pytest.mark.parametrize("text", ["Xylofrobinate", "Ксилофробинат; ---"])
async def test_a_valid_unknown_ingredient_name_is_usable_without_curation(app_client, monkeypatch, endpoint, text):
    """Unknown != unusable, in Latin and non-Latin scripts alike."""
    resp = await _post(app_client, monkeypatch, endpoint, text, f"rf-unknown-{endpoint}-{abs(hash(text))}")
    assert resp.status_code == 200, resp.text
    (ing,) = resp.json()["ingredients"]
    assert ing["commonName"] in ("Xylofrobinate", "Ксилофробинат")
    assert ing["description"] == ""
    assert ing["riskAssessmentAvailable"] is False


# --- 3. Barcode-linked: image and OCR -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
@pytest.mark.parametrize("text", UNRESOLVABLE)
async def test_barcode_linked_unresolvable_text_is_resolution_failed(
    app_client, monkeypatch, db_session, endpoint, text
):
    barcode = {"label-image": BARCODE_A, "ocr-text": BARCODE_B}[endpoint]
    resp = await _post(app_client, monkeypatch, endpoint, text, f"rf-linked-{endpoint}-{abs(hash(text))}", barcode=barcode)
    details = _assert_resolution_failed(resp)

    assert details["discoveredIdentity"]["barcode"] == barcode
    assert details["ingredientsScanRequired"] is True
    assert "ingredients" not in details  # a new row has nothing to hand back
    stored = await _product(db_session, barcode)
    assert stored.has_verified_ingredients is False
    assert await _count(db_session, ScanHistory) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
async def test_a_later_real_list_completes_the_row_a_failed_attempt_left_open(
    app_client, monkeypatch, db_session, endpoint
):
    barcode = BARCODE_C
    first = await _post(app_client, monkeypatch, endpoint, "---", f"rf-retry-1-{endpoint}", barcode=barcode)
    _assert_resolution_failed(first)

    second = await _post(app_client, monkeypatch, endpoint, ENGLISH_LABEL, f"rf-retry-2-{endpoint}", barcode=barcode)
    assert second.status_code == 200, second.text
    assert "Sugar" in _names(second.json())
    stored = await _product(db_session, barcode)
    assert stored.has_verified_ingredients is True
    assert await _count(db_session, Product, Product.barcode == barcode) == 1


@pytest.mark.asyncio
async def test_barcode_linked_nutrition_survives_a_resolution_failure_without_claiming_ingredients(
    app_client, monkeypatch, db_session
):
    """Nutrition-only evidence is kept; the ingredient group is not
    pretended complete. Image path only: `/scan/ocr-text` can never
    complete the nutrition group."""
    resp = await _post(
        app_client, monkeypatch, "label-image", "---", "rf-nutrition", barcode=BARCODE_D, extra=FULL_NUTRITION
    )
    details = _assert_resolution_failed(resp)
    assert details["nutritionScanRequired"] is False
    assert details["ingredientsScanRequired"] is True
    stored = await _product(db_session, BARCODE_D)
    assert stored.sugar_grams == 9.0
    assert stored.has_verified_nutrition is True
    assert stored.has_verified_ingredients is False


# --- 4. Existing products keep their evidence ---------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
async def test_failed_attempt_never_replaces_a_verified_product(app_client, monkeypatch, db_session, endpoint):
    barcode = BARCODE_E
    first = await _scan(
        app_client,
        monkeypatch,
        {"productName": "Bolt Energy Drink", "rawIngredientText": ENGLISH_LABEL, **FULL_NUTRITION},
        f"rf-keep-1-{endpoint}",
        barcode=barcode,
    )
    assert first.status_code == 200, first.text
    before = await _product(db_session, barcode)
    snapshot = {
        column: getattr(before, column)
        for column in (
            "product_name", "raw_ingredient_text", "original_ingredient_text", "ingredient_ids",
            "sugar_grams", "sodium_mg", "saturated_fat_grams", "nova_group", "allergens_detected",
            "has_verified_ingredients", "has_verified_nutrition", "is_verified", "source",
        )
    }
    assert snapshot["has_verified_ingredients"] and snapshot["has_verified_nutrition"]
    provenance_before = (
        await db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    raw_before = {row.provider: row.raw_ingredient_text for row in provenance_before}
    assert raw_before["label_ocr"] == ENGLISH_LABEL
    history_before = await _count(db_session, ScanHistory)

    calls = _capture(monkeypatch)
    resp = await _post(app_client, monkeypatch, endpoint, "---", f"rf-keep-2-{endpoint}", barcode=barcode)
    details = _assert_resolution_failed(resp)

    # The envelope hands the preserved ingredients back for the client.
    assert "Sugar" in [ing["commonName"] for ing in details["ingredients"]]
    assert details["ingredientsScanRequired"] is False
    assert details["nutritionScanRequired"] is False
    assert "previously saved ingredients were kept" in details["reason"]

    db_session.expire_all()
    after = await _product(db_session, barcode)
    assert {column: getattr(after, column) for column in snapshot} == snapshot
    assert await _count(db_session, Product, Product.barcode == barcode) == 1
    assert await _count(db_session, ScanHistory) == history_before  # a failed scan is not history

    # Earlier provenance (the source text of the successful scan) is not
    # overwritten. A different provider (the OCR endpoint after an image
    # scan) gets its own row for the failed attempt, marked as not used.
    provenance_after = (
        await db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))
    ).scalars().all()
    raw_after = {row.provider: row.raw_ingredient_text for row in provenance_after}
    assert {p: raw_after[p] for p in raw_before} == raw_before
    for row in provenance_after:
        if row.provider not in raw_before:
            assert row.used_for_persisted_product is False

    # The failed attempt is still recorded, content-free, in the diagnostics journal.
    assert calls[-1]["failureReason"] == "RESOLUTION_FAILED"
    assert calls[-1]["outcome"] == "partial"
    assert "---" not in str(calls[-1])


@pytest.mark.asyncio
async def test_failed_attempt_keeps_the_ingredient_text_of_a_not_yet_verified_product(
    app_client, monkeypatch, db_session
):
    """Even a row that is not verified keeps its existing ingredient text
    and ids -- a failed attempt is not evidence to replace them with."""
    barcode = "4260107010029"
    db_session.add(
        Product(
            barcode=barcode,
            product_name="Discovered Product",
            brand="Unknown Brand",
            category="",
            raw_ingredient_text="Water, Sugar",
            ingredient_ids="",
            health_score=0,
            sugar_grams=0.0,
            sodium_mg=0.0,
            saturated_fat_grams=0.0,
            nova_group=1,
            timestamp=1,
            source="open_food_facts",
            is_verified=False,
            has_verified_nutrition=False,
            has_verified_ingredients=False,
        )
    )
    await db_session.commit()

    resp = await _post(app_client, monkeypatch, "ocr-text", "; ; ,", "rf-unverified", barcode=barcode)
    _assert_resolution_failed(resp)

    db_session.expire_all()
    after = await _product(db_session, barcode)
    assert after.raw_ingredient_text == "Water, Sugar"
    assert after.has_verified_ingredients is False
    # This attempt contributed nothing to the persisted product, and its
    # provenance row says so.
    rows = (await db_session.execute(select(ProductSource).where(ProductSource.barcode == barcode))).scalars().all()
    assert [(row.provider, row.used_for_persisted_product) for row in rows] == [("ocr_text", False)]


# --- 5. Bounded, content-free diagnostics -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["label-image", "ocr-text"])
@pytest.mark.parametrize("barcode", [None, "6291041500213"])
async def test_diagnostics_record_the_resolution_failure_without_content(app_client, monkeypatch, endpoint, barcode):
    calls = _capture(monkeypatch)
    resp = await _post(app_client, monkeypatch, endpoint, "1234, 5678", f"rf-diag-{endpoint}-{barcode}", barcode=barcode)
    _assert_resolution_failed(resp)

    assert len(calls) == 1
    record = calls[0]
    assert record["failureReason"] == "RESOLUTION_FAILED"
    assert record["outcome"] == "partial"
    assert record["errorCode"] == "PRODUCT_NOT_FOUND"
    if endpoint == "label-image":
        assert record["labelExtraction"] == "extracted"  # extraction succeeded; resolution did not
    assert "1234" not in str(record) and "5678" not in str(record)


@pytest.mark.asyncio
async def test_resolution_failed_is_a_member_of_the_published_vocabulary():
    from app.core.exceptions import ScanFailureReason

    assert ScanFailureReason.RESOLUTION_FAILED.value == "RESOLUTION_FAILED"
