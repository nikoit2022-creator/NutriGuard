"""
Regression tests for the V2 bug-fix task: `POST /api/v1/scan/barcode` must
never fabricate a product for an unknown barcode, and the Health
Score / Warning Engine must be provably correct for a real, persisted
product (not just unit-tested in isolation).

Covers TEST 1-6 from the task's required regression-test list.
TEST 7 and TEST 8 (existing auth/API tests still pass) are satisfied by
the pre-existing `tests/integration/test_auth.py` and the rest of
`tests/integration/test_scan_flow.py` continuing to pass unmodified.
"""
import pytest


async def _register_device(client, device_id="barcode-fix-device"):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


# TEST 1 + TEST 2 -----------------------------------------------------------
# Unknown barcode must not create a synthetic product, and must not
# return isFromDatabaseCache=true (in fact, no such field at all: the
# response is a 404 error envelope, not a FullProductAnalysis).

@pytest.mark.asyncio
async def test_unknown_barcode_does_not_fabricate_product_or_claim_cache_hit(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "3903792380169"}, headers=headers
    )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert "isFromDatabaseCache" not in body
    assert "product" not in body
    assert "Scanned Item" not in resp.text  # the old fabricated-name pattern must never appear


# TEST 3 ----------------------------------------------------------------------
# A known product (persisted via the OCR pipeline, the correct entry
# point for analyzing label content) returns the real stored product.

@pytest.mark.asyncio
async def test_known_product_returns_real_stored_product(app_client):
    headers = await _register_device(app_client, device_id="known-product-device-2")

    seed = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Sodium Nitrite, Corn Syrup"},
        headers=headers,
    )
    barcode = seed.json()["product"]["barcode"]
    raw_text = seed.json()["product"]["rawIngredientText"]

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["isFromDatabaseCache"] is True
    assert body["product"]["barcode"] == barcode
    assert body["product"]["rawIngredientText"] == raw_text
    # No fabricated placeholder text anywhere in the real response.
    assert "Newly Identified Product" not in resp.text


# TEST 4 ------------------------------------------------------------------------
# Health Score is calculated using the existing formula for a real
# product, with a hand-verified expected value (see PR description /
# task response for the full derivation):
#
#   ingredients: [HIGH_CONCERN, POTENTIAL_CONCERN]   -> additives = 20 + 12 = 32
#   sugar_grams=2.0  (not > 2.0)                      -> sugar_deduction = 0
#   sodium_mg=450.0  (> 300, not > 600)                -> sodium_deduction = 10
#   saturated_fat=0.5 (not > 2.5)                       -> sat_fat_deduction = 0
#   has_artificial_sweeteners=False                      -> sweetener_deduction = 0
#   has_preservatives=True ("nitrit" keyword)             -> preservative_deduction = 10
#   nova_group=4 (has_preservatives=True)                  -> nova_deduction = 20
#   total = 100 - (32+0+10+0+0+10+20) = 100 - 72 = 28

@pytest.mark.asyncio
async def test_health_score_matches_hand_verified_formula_for_real_product(app_client):
    headers = await _register_device(app_client, device_id="health-score-regression-device")

    ocr_resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Sodium Nitrite, Corn Syrup"},
        headers=headers,
    )
    assert ocr_resp.status_code == 200
    assert ocr_resp.json()["healthScore"] == 28

    barcode = ocr_resp.json()["product"]["barcode"]
    barcode_resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers
    )
    assert barcode_resp.status_code == 200
    # The score must be IDENTICAL when recomputed from the persisted
    # product+ingredients on a barcode lookup, not reset to a default.
    assert barcode_resp.json()["healthScore"] == 28

    risk_levels = {ing["riskLevel"] for ing in barcode_resp.json()["ingredients"]}
    assert "HIGH_CONCERN" in risk_levels
    assert "POTENTIAL_CONCERN" in risk_levels


# TEST 5 --------------------------------------------------------------------
# Personalized Warning Engine produces expected warnings for a real
# product once the relevant health-profile flag is enabled.

@pytest.mark.asyncio
async def test_warnings_generated_for_real_product_after_enabling_profile_flag(app_client):
    headers = await _register_device(app_client, device_id="warning-regression-device")

    ocr_resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Sodium Nitrite, Corn Syrup"},
        headers=headers,
    )
    barcode = ocr_resp.json()["product"]["barcode"]

    # Baseline: no health conditions enabled -> no warnings.
    assert ocr_resp.json()["warnings"] == []

    profile_resp = await app_client.put(
        "/api/v1/health-profile", json={"hasHypertension": True}, headers=headers
    )
    assert profile_resp.status_code == 200

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    warnings = resp.json()["warnings"]
    hypertension_warnings = [w for w in warnings if w["condition"] == "Hypertension"]

    # Expect both the product-level sodium threshold warning (450mg > 400)
    # AND the ingredient-flag warning (Sodium Nitrite -> bad_for_hypertension).
    assert len(hypertension_warnings) == 2
    severities = {w["severity"] for w in hypertension_warnings}
    assert "MODERATE" in severities  # 450mg is > 400 but not > 800
    assert "HIGH" in severities  # ingredient-flag warnings are always HIGH


# TEST 6 -------------------------------------------------------------------------
# Barcode lookup must not trigger the OCR/synthetic-ingredient pipeline
# for an UNKNOWN barcode (no Gemini call, no fallback analysis, no
# synthetic ingredient creation at all for that request).

@pytest.mark.asyncio
async def test_unknown_barcode_never_invokes_synthetic_ingredient_pipeline(app_client, monkeypatch):
    import app.services.food_analysis as food_analysis_module

    called = {"create_synthetic": False, "fallback": False}

    def _spy_create_synthetic(*args, **kwargs):
        called["create_synthetic"] = True
        raise AssertionError("create_synthetic_ingredient must not be called for an unknown barcode")

    def _spy_fallback(*args, **kwargs):
        called["fallback"] = True
        raise AssertionError("fallback_local_analysis must not be called for an unknown barcode")

    monkeypatch.setattr(food_analysis_module, "create_synthetic_ingredient", _spy_create_synthetic)
    monkeypatch.setattr(food_analysis_module, "fallback_local_analysis", _spy_fallback)

    headers = await _register_device(app_client, device_id="no-synthetic-pipeline-device")
    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "0000000000000"}, headers=headers
    )

    assert resp.status_code == 404
    assert called["create_synthetic"] is False
    assert called["fallback"] is False
