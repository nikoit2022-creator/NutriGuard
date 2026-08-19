import pytest


async def _register_device(client, device_id="scan-device"):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_scan_ocr_text_returns_full_analysis(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, High Fructose Corn Syrup, Sodium Benzoate (E211)"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "product" in body
    assert "ingredients" in body
    assert isinstance(body["healthScore"], int)
    assert 0 <= body["healthScore"] <= 100
    assert body["isFromDatabaseCache"] is False
    assert body["product"]["rawIngredientText"].startswith("Water")


@pytest.mark.asyncio
async def test_scan_ocr_text_rejects_too_short_text(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "ab"}, headers=headers)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_scan_barcode_unknown_returns_404_not_found(app_client):
    """CONTRACT CHANGE regression test: an unknown barcode must NOT
    fabricate a product. See food_analysis.analyze_barcode docstring."""
    headers = await _register_device(app_client)
    barcode = "3903792380169"

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "PRODUCT_NOT_FOUND"
    assert barcode in body["error"]["message"]
    # The envelope must never carry a fabricated product/analysis.
    assert "product" not in body
    assert "ingredients" not in body
    assert "healthScore" not in body
    assert "isFromDatabaseCache" not in body


@pytest.mark.asyncio
async def test_scan_barcode_unknown_does_not_create_a_product_record(app_client):
    """A failed barcode lookup must have no side effects: no product row,
    no scan-history entry."""
    headers = await _register_device(app_client, device_id="no-side-effects-device")
    barcode = "9999999999999"

    scan_resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert scan_resp.status_code == 404

    # Confirm no product was persisted for this barcode.
    product_resp = await app_client.get(f"/api/v1/products/{barcode}", headers=headers)
    assert product_resp.status_code == 404
    assert product_resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    # Confirm no scan-history entry was written for the failed lookup.
    history_resp = await app_client.get("/api/v1/scan-history", headers=headers)
    assert history_resp.json()["totalItems"] == 0


@pytest.mark.asyncio
async def test_scan_barcode_known_product_returns_stored_data_with_cache_true(app_client):
    """A barcode that DOES exist (created here via the OCR-text pipeline,
    which is the correct entry point for analyzing label content) must be
    returned with isFromDatabaseCache=true and the real stored product."""
    headers = await _register_device(app_client, device_id="known-product-device")

    seed_scan = await app_client.post(
        "/api/v1/scan/ocr-text",
        json={"rawText": "Water, High Fructose Corn Syrup, Aspartame (E951), Sodium Nitrite (E250)"},
        headers=headers,
    )
    assert seed_scan.status_code == 200
    barcode = seed_scan.json()["product"]["barcode"]
    original_ingredient_ids = seed_scan.json()["product"]["ingredientIds"]

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["isFromDatabaseCache"] is True
    assert body["product"]["barcode"] == barcode
    assert body["product"]["ingredientIds"] == original_ingredient_ids


@pytest.mark.asyncio
async def test_scan_barcode_rejects_empty_barcode(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": "  "}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_health_profile_changes_warnings_on_next_scan(app_client):
    headers = await _register_device(app_client, device_id="diabetic-user-device")

    # Baseline: no health conditions -> analyze a high-sugar product.
    scan1 = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar, Salt"}, headers=headers
    )
    assert scan1.status_code == 200
    baseline_warning_count = len(scan1.json()["warnings"])

    # Enable diabetes flag.
    profile_resp = await app_client.put(
        "/api/v1/health-profile", json={"hasDiabetes": True}, headers=headers
    )
    assert profile_resp.status_code == 200
    assert profile_resp.json()["hasDiabetes"] is True

    barcode = scan1.json()["product"]["barcode"]
    scan2 = await app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert scan2.status_code == 200
    assert scan2.json()["isFromDatabaseCache"] is True
    diabetic_warnings = [w for w in scan2.json()["warnings"] if w["condition"] == "Diabetes"]
    assert len(diabetic_warnings) >= 1
    assert len(scan2.json()["warnings"]) > baseline_warning_count


@pytest.mark.asyncio
async def test_get_health_profile_returns_defaults_when_absent(app_client):
    headers = await _register_device(app_client, device_id="fresh-device")
    resp = await app_client.get("/api/v1/health-profile", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["hasDiabetes"] is False
    assert body["id"] == 1


@pytest.mark.asyncio
async def test_scan_history_records_and_lists(app_client):
    headers = await _register_device(app_client, device_id="history-device")
    await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "Water, Salt"}, headers=headers)
    await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "Milk, Sugar"}, headers=headers)

    history = await app_client.get("/api/v1/scan-history", headers=headers)
    assert history.status_code == 200
    body = history.json()
    assert body["totalItems"] == 2
    assert len(body["items"]) == 2
    assert body["items"][0]["scanType"] == "OCR_LABEL"


@pytest.mark.asyncio
async def test_scan_history_isolated_per_user(app_client):
    headers_a = await _register_device(app_client, device_id="user-a")
    headers_b = await _register_device(app_client, device_id="user-b")

    await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "Water, Salt"}, headers=headers_a)

    hist_a = await app_client.get("/api/v1/scan-history", headers=headers_a)
    hist_b = await app_client.get("/api/v1/scan-history", headers=headers_b)
    assert hist_a.json()["totalItems"] == 1
    assert hist_b.json()["totalItems"] == 0


@pytest.mark.asyncio
async def test_clear_scan_history(app_client):
    headers = await _register_device(app_client, device_id="clear-device")
    await app_client.post("/api/v1/scan/ocr-text", json={"rawText": "Water, Salt"}, headers=headers)

    delete_resp = await app_client.delete("/api/v1/scan-history", headers=headers)
    assert delete_resp.status_code == 204

    history = await app_client.get("/api/v1/scan-history", headers=headers)
    assert history.json()["totalItems"] == 0


@pytest.mark.asyncio
async def test_get_product_not_found(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.get("/api/v1/products/does-not-exist", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_product_after_scan(app_client):
    headers = await _register_device(app_client)
    scan = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Salt"}, headers=headers
    )
    barcode = scan.json()["product"]["barcode"]

    resp = await app_client.get(f"/api/v1/products/{barcode}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["product"]["barcode"] == barcode


@pytest.mark.asyncio
async def test_list_products_pagination(app_client):
    headers = await _register_device(app_client)
    for text in ["Water, Salt", "Milk, Sugar", "Cocoa, Nuts"]:
        await app_client.post("/api/v1/scan/ocr-text", json={"rawText": text}, headers=headers)

    resp = await app_client.get("/api/v1/products?page=1&pageSize=2", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert len(body["items"]) == 2
    assert body["totalItems"] == 3
    assert body["totalPages"] == 2


@pytest.mark.asyncio
async def test_ingredient_lookup_not_found(app_client):
    headers = await _register_device(app_client)
    resp = await app_client.get("/api/v1/ingredients/does-not-exist", headers=headers)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "INGREDIENT_NOT_FOUND"
