"""
Issue #25 -- ingredient-first label scanning: recognized ingredients are
returned independently of product identity (name/barcode) and nutrition
completeness. Every assertion is on the actual HTTP JSON.

Root cause these tests pin (reproduced on main 32bd7ef): the label-image
parser rejected a whole, otherwise valid Gemini extraction when
`productName` was empty or missing, so the deterministic fallback ran on
a placeholder string and the response became the `404`
`labelScanRequired` "Scanned Label Product" result with no ingredients --
even with a complete nutrition panel. See
`docs/INGREDIENT_FIRST_LABEL_SCAN.md`.

Every Gemini call is mocked; no network, no live data.
"""
import io
import json
from datetime import datetime, timezone

import pytest
from PIL import Image
from sqlalchemy import func, select

import app.api.v1.scan as scan_module
from app.integrations.gemini import GeminiUnavailableError, gemini_service
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
from app.repositories import ingredient_alias_repository
from app.services.ingredient_localization import canonical_text_hash

INGREDIENTS = "Contains: Oats, Sugar, Citric Acid"
FULL_NUTRITION = {
    "sugarGrams": 9.0,
    "sodiumMg": 55.0,
    "saturatedFatGrams": 1.2,
    "nutritionBasis": "PER_100_G",
}
# Detected as English by the label-language policy, so the strict
# barcode-linked path never needs the (unavailable) translation service.
ENGLISH_LABEL = "Carbonated Water, Sugar, Citric Acid, Sodium Benzoate (E211)"
# Checksum-valid EAN-13s not used by any other test file.
BARCODE_NEW = "4006381333931"
BARCODE_TRUSTED = "5901234123457"
BARCODE_ENRICH = "9780201379624"
IDENTITY_FOUND_REASON_PREFIX = "This product's identity was found"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


def _mock_image(monkeypatch, payload) -> None:
    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return payload if isinstance(payload, str) else json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)


async def _headers(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


async def _scan(client, monkeypatch, payload, device_id: str, *, barcode: str | None = None):
    _mock_image(monkeypatch, payload)
    return await client.post(
        "/api/v1/scan/label-image",
        headers=await _headers(client, device_id),
        files={"image": ("label.jpg", _jpeg(), "image/jpeg")},
        data={"barcode": barcode} if barcode is not None else None,
    )


def _names(body: dict) -> list[str]:
    return [ing["commonName"] for ing in body["ingredients"]]


async def _seed_curated_citric_acid_with_reviewed_bg(db_session) -> None:
    """Minimal curated catalog row + REVIEWED Bulgarian localization,
    mirroring what `app/seed/load_seed.py` persists (same shape as
    `test_ingredient_bilingual_contract`'s helper)."""
    curated = Ingredient(
        id="e330_citric_acid",
        common_name="Citric Acid",
        normalized_name="citric acid",
        scientific_name="2-hydroxypropane-1,2,3-tricarboxylic acid",
        e_number="E330",
        category="Acidity Regulator",
        description="A weak organic acid used as a natural preservative and flavoring.",
        purpose_in_food="Acidity regulator, preservative, flavoring.",
        health_concerns="",
        evidence_level="Strong Scientific Consensus",
        countries_restricted_or_banned="",
        efsa_status="Authorized (No ADI limit necessary)",
        fda_status="GRAS",
        acceptable_daily_intake="Not limited",
        side_effects="",
        allergens="None",
        references="EFSA Journal 2016;14(3):4416",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=True,
        verification_status=IngredientVerificationStatus.VERIFIED,
        source=IngredientSource.CURATED_SEED,
        confidence=1.0,
    )
    db_session.add(curated)
    await db_session.flush()
    await ingredient_alias_repository.get_or_create(
        db_session,
        ingredient_id=curated.id,
        alias_text=curated.common_name,
        alias_normalized=curated.normalized_name,
        language="en",
        source=IngredientSource.CURATED_SEED,
    )
    db_session.add(
        IngredientLocalization(
            ingredient_id=curated.id,
            language="bg",
            common_name="Лимонена киселина",
            category="Регулатор на киселинност",
            description="Слаба органична киселина.",
            purpose_in_food="Регулатор на киселинност, консервант.",
            health_concerns="",
            evidence_level="Силен научен консенсус",
            countries_restricted_or_banned="",
            efsa_status="Разрешен",
            fda_status="GRAS",
            acceptable_daily_intake="Неограничен",
            side_effects="",
            allergens="Няма",
            translation_status=IngredientTranslationStatus.REVIEWED,
            translation_source=IngredientTranslationSource.HUMAN_CURATED,
            source_content_hash=canonical_text_hash(curated),
            reviewed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()


# --- 1. The reproduced defect: identity completeness no longer gates evidence ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case, name_fields, nutrition, expect_score",
    [
        ("named_ingredients_only", {"productName": "Harvest Bar"}, {}, False),
        ("empty_name_ingredients_only", {"productName": ""}, {}, False),
        ("missing_name_ingredients_only", {}, {}, False),
        ("empty_name_ingredients_and_full_nutrition", {"productName": ""}, FULL_NUTRITION, True),
        ("named_ingredients_and_full_nutrition", {"productName": "Harvest Bar"}, FULL_NUTRITION, True),
    ],
)
async def test_probe_matrix_returns_ingredients_regardless_of_product_name(
    app_client, monkeypatch, case, name_fields, nutrition, expect_score
):
    payload = {"rawIngredientText": INGREDIENTS, **name_fields, **nutrition}
    resp = await _scan(app_client, monkeypatch, payload, f"i25-{case}")

    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert _names(body) == ["Oats", "Sugar", "Citric Acid"]
    assert body["product"]["productName"] == name_fields.get("productName", "")
    assert body["product"]["barcode"].startswith("img_")  # never a fake real barcode
    assert body["product"]["hasVerifiedIngredients"] is True
    assert body["product"]["hasVerifiedNutrition"] is expect_score
    if expect_score:
        assert isinstance(body["healthScore"], int)
    else:
        assert body["healthScore"] is None
        assert body["product"]["nutritionBasis"] == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("name_value", [None, 123, "null", "Unknown", "   "])
async def test_non_string_or_placeholder_name_is_unobserved_not_invented(app_client, monkeypatch, name_value):
    payload = {"productName": name_value, "rawIngredientText": INGREDIENTS}
    resp = await _scan(app_client, monkeypatch, payload, f"i25-placeholder-{name_value!r}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["productName"] == ""
    assert _names(body) == ["Oats", "Sugar", "Citric Acid"]


@pytest.mark.asyncio
async def test_nameless_result_carries_exactly_the_same_ingredient_evidence_as_named(app_client, monkeypatch):
    """Dropping the identity gate must not change any ingredient-level
    field: no verification/risk/translation promotion, identical JSON."""
    named = (await _scan(
        app_client, monkeypatch, {"productName": "Harvest Bar", "rawIngredientText": INGREDIENTS}, "i25-parity-a"
    )).json()
    nameless = (await _scan(
        app_client, monkeypatch, {"rawIngredientText": INGREDIENTS}, "i25-parity-b"
    )).json()

    def _stable(ings):
        return [{k: v for k, v in ing.items() if k != "retrievedAt"} for ing in ings]

    assert _stable(nameless["ingredients"]) == _stable(named["ingredients"])
    for ing in nameless["ingredients"]:
        assert ing["verificationStatus"] == "UNVERIFIED"
        assert ing["riskAssessmentAvailable"] is False
        assert ing["description"] == ""  # unknown descriptions stay empty


# --- 2. Partial / mixed recognition and catalog reuse ---------------------------


@pytest.mark.asyncio
async def test_mixed_curated_and_unknown_tokens_keep_both_and_reuse_the_catalog(
    app_client, monkeypatch, db_session
):
    await _seed_curated_citric_acid_with_reviewed_bg(db_session)
    payload = {"productName": "", "rawIngredientText": "Citric Acid, Xylofrobinate"}
    resp = await _scan(app_client, monkeypatch, payload, "i25-mixed")

    assert resp.status_code == 200, resp.json()
    by_name = {ing["commonName"]: ing for ing in resp.json()["ingredients"]}
    assert set(by_name) == {"Citric Acid", "Xylofrobinate"}

    curated = by_name["Citric Acid"]
    assert curated["id"] == "e330_citric_acid"  # canonical catalog row reused
    assert curated["verificationStatus"] == "VERIFIED"
    assert curated["localizations"]["bg"]["commonName"] == "Лимонена киселина"  # reviewed BG fallback

    unknown = by_name["Xylofrobinate"]
    assert unknown["id"].startswith("synth_")
    assert unknown["verificationStatus"] == "UNVERIFIED"
    assert unknown["riskAssessmentAvailable"] is False
    assert unknown["description"] == ""
    assert "bg" not in unknown["localizations"]  # never a fabricated translation


# --- 3. Languages ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulgarian_label_without_name_resolves_through_existing_aliases(app_client, monkeypatch):
    resp = await _scan(app_client, monkeypatch, {"rawIngredientText": "Вода, Захар, Сол"}, "i25-bg")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert sorted(_names(body)) == ["Salt", "Sugar", "Water"]
    assert body["product"]["productName"] == ""
    assert "Захар" in body["product"]["originalIngredientText"]  # original text retained


@pytest.mark.asyncio
async def test_third_language_label_without_name_is_translated(app_client, monkeypatch):
    async def fake_translate(text: str) -> str:
        return json.dumps(
            {"detectedLanguage": "de", "confidence": 0.9, "translatedText": "Water, Sugar, Salt"}
        )

    monkeypatch.setattr(gemini_service, "translate_label_text", fake_translate)
    resp = await _scan(app_client, monkeypatch, {"rawIngredientText": "Wasser, Zucker, Salz"}, "i25-de")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert sorted(_names(body)) == ["Salt", "Sugar", "Water"]
    assert body["product"]["productName"] == ""
    assert "Wasser" in body["product"]["originalIngredientText"]


# --- 4. Zero useful ingredients / nutrition-only -------------------------------


@pytest.mark.asyncio
async def test_empty_or_blurry_label_gives_honest_retry_guidance(app_client, monkeypatch):
    resp = await _scan(app_client, monkeypatch, {"productName": "", "rawIngredientText": ""}, "i25-empty")
    assert resp.status_code == 404
    details = resp.json()["error"]["details"]
    assert details["labelScanRequired"] is True
    assert details["ingredientsScanRequired"] is True
    assert "ingredients" not in details  # nothing fabricated
    assert not details["reason"].startswith(IDENTITY_FOUND_REASON_PREFIX)
    assert "No ingredients could be read" in details["reason"]


@pytest.mark.asyncio
async def test_nameless_nutrition_only_panel_is_kept_without_claiming_ingredient_success(
    app_client, monkeypatch, db_session
):
    resp = await _scan(
        app_client, monkeypatch, {"rawIngredientText": "", **FULL_NUTRITION}, "i25-nutrition-only"
    )
    assert resp.status_code == 404
    details = resp.json()["error"]["details"]
    assert details["nutritionScanRequired"] is False
    assert details["ingredientsScanRequired"] is True
    assert "ingredients" not in details
    assert not details["reason"].startswith(IDENTITY_FOUND_REASON_PREFIX)

    stored = (
        await db_session.execute(select(Product).where(Product.barcode == details["discoveredIdentity"]["barcode"]))
    ).scalar_one()
    assert stored.sugar_grams == 9.0
    assert stored.has_verified_nutrition is True
    assert stored.has_verified_ingredients is False


# --- 5. Barcode-linked captures ---------------------------------------------------


@pytest.mark.asyncio
async def test_barcode_linked_nameless_label_returns_ingredients_on_the_canonical_barcode(app_client, monkeypatch):
    resp = await _scan(
        app_client, monkeypatch, {"rawIngredientText": ENGLISH_LABEL}, "i25-linked-new", barcode=BARCODE_NEW
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["product"]["barcode"] == BARCODE_NEW
    assert body["product"]["productName"] == ""
    assert "Sugar" in _names(body)
    assert body["healthScore"] is None


@pytest.mark.asyncio
async def test_barcode_linked_nameless_label_never_replaces_trusted_evidence(app_client, monkeypatch, db_session):
    first = await _scan(
        app_client,
        monkeypatch,
        {"productName": "Bolt Energy Drink", "rawIngredientText": ENGLISH_LABEL, **FULL_NUTRITION},
        "i25-trusted-1",
        barcode=BARCODE_TRUSTED,
    )
    assert first.status_code == 200
    assert first.json()["product"]["isVerified"] is True

    second = await _scan(
        app_client,
        monkeypatch,
        {"rawIngredientText": "Carbonated Water, Sugar, Citric Acid"},
        "i25-trusted-2",
        barcode=BARCODE_TRUSTED,
    )
    assert second.status_code == 200, second.json()
    product = second.json()["product"]
    assert product["productName"] == "Bolt Energy Drink"  # nameless capture never erases identity
    assert product["sugarGrams"] == 9.0  # verified nutrition kept
    assert product["hasVerifiedNutrition"] is True
    assert isinstance(second.json()["healthScore"], int)

    count = (
        await db_session.execute(select(func.count()).select_from(Product).where(Product.barcode == BARCODE_TRUSTED))
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_later_named_capture_enriches_the_same_nameless_product(app_client, monkeypatch, db_session):
    first = await _scan(
        app_client, monkeypatch, {"rawIngredientText": ENGLISH_LABEL}, "i25-enrich-1", barcode=BARCODE_ENRICH
    )
    assert first.status_code == 200
    assert first.json()["product"]["productName"] == ""

    second = await _scan(
        app_client,
        monkeypatch,
        {"productName": "Bolt Energy Drink", "rawIngredientText": ENGLISH_LABEL, **FULL_NUTRITION},
        "i25-enrich-2",
        barcode=BARCODE_ENRICH,
    )
    assert second.status_code == 200
    assert second.json()["product"]["productName"] == "Bolt Energy Drink"
    assert second.json()["product"]["barcode"] == BARCODE_ENRICH
    count = (
        await db_session.execute(select(func.count()).select_from(Product).where(Product.barcode == BARCODE_ENRICH))
    ).scalar_one()
    assert count == 1


# --- 6. OCR-text endpoint parity ------------------------------------------------


@pytest.mark.asyncio
async def test_ocr_text_endpoint_returns_ingredients_without_identity_or_nutrition(app_client):
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        headers=await _headers(app_client, "i25-ocr"),
        json={"rawText": "Oats, Sugar, Citric Acid"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert _names(body) == ["Oats", "Sugar", "Citric Acid"]
    assert body["healthScore"] is None
    assert body["product"]["barcode"].startswith("ocr_")


# --- 7. Bounded, content-free diagnostics ---------------------------------------


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(scan_module, "record_scan_diagnostic", lambda **fields: calls.append(fields))
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload, status, extraction, identity, nutrition",
    [
        ({"rawIngredientText": INGREDIENTS}, 200, "extracted", False, False),
        ({"productName": "Harvest Bar", "rawIngredientText": INGREDIENTS, **FULL_NUTRITION}, 200, "extracted", True, True),
        ({"productName": "", "rawIngredientText": ""}, 404, "model_response_empty", False, False),
        ("not json {{{", 404, "model_response_invalid", False, False),
    ],
)
async def test_diagnostics_distinguish_extraction_outcomes_without_content(
    app_client, monkeypatch, payload, status, extraction, identity, nutrition
):
    calls = _capture(monkeypatch)
    resp = await _scan(app_client, monkeypatch, payload, f"i25-diag-{extraction}-{identity}")
    assert resp.status_code == status
    assert len(calls) == 1
    record = calls[0]
    assert record["labelExtraction"] == extraction
    assert record["productIdentityObserved"] is identity
    assert record["labelNutritionComplete"] is nutrition
    serialized = json.dumps(record, default=str)
    for fragment in ("Oats", "Harvest", "Contains", "not json"):
        assert fragment not in serialized


@pytest.mark.asyncio
async def test_diagnostics_report_model_unavailable(app_client, monkeypatch):
    calls = _capture(monkeypatch)

    async def unavailable(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        raise GeminiUnavailableError("simulated")

    monkeypatch.setattr(gemini_service, "analyze_image", unavailable)
    resp = await app_client.post(
        "/api/v1/scan/label-image",
        headers=await _headers(app_client, "i25-diag-unavailable"),
        files={"image": ("label.jpg", _jpeg(), "image/jpeg")},
    )
    assert resp.status_code == 404
    assert calls[0]["labelExtraction"] == "model_unavailable"
    assert calls[0]["productIdentityObserved"] is False


def test_diagnostic_fields_never_reach_the_wire():
    from app.schemas.scan import FullProductAnalysisOut

    fields = set(FullProductAnalysisOut.model_json_schema(by_alias=True)["properties"])
    assert not fields & {"labelExtraction", "productIdentityObserved", "labelNutritionComplete"}
