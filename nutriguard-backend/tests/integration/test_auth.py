import pytest


@pytest.mark.asyncio
async def test_device_auth_issues_tokens(app_client):
    resp = await app_client.post(
        "/api/v1/auth/device",
        json={"deviceId": "device-123", "appVersion": "1.0", "platform": "ANDROID"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accessToken"]
    assert body["refreshToken"]
    assert body["tokenType"] == "Bearer"
    assert body["expiresIn"] > 0
    assert body["userId"]


@pytest.mark.asyncio
async def test_device_auth_is_idempotent_for_same_device_id(app_client):
    r1 = await app_client.post("/api/v1/auth/device", json={"deviceId": "same-device"})
    r2 = await app_client.post("/api/v1/auth/device", json={"deviceId": "same-device"})
    assert r1.json()["userId"] == r2.json()["userId"]


@pytest.mark.asyncio
async def test_protected_endpoint_requires_token(app_client):
    resp = await app_client.get("/api/v1/health-profile")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_protected_endpoint_rejects_garbage_token(app_client):
    resp = await app_client.get(
        "/api/v1/health-profile", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_refresh_token_flow(app_client):
    auth_resp = await app_client.post("/api/v1/auth/device", json={"deviceId": "refresh-device"})
    refresh_token = auth_resp.json()["refreshToken"]

    refresh_resp = await app_client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})
    assert refresh_resp.status_code == 200
    new_tokens = refresh_resp.json()
    assert new_tokens["accessToken"]

    # Old refresh token must be revoked after rotation (single-use).
    reuse_resp = await app_client.post("/api/v1/auth/refresh", json={"refreshToken": refresh_token})
    assert reuse_resp.status_code == 401
