"""
Full HTTP coverage of the X-Scan-Attempt-Id / X-Scan-Request-Sequence
header contract on the three `/scan/*` endpoints (issue #30, see
docs/SCAN_ATTEMPT_DIAGNOSTICS.md).
"""
import re

import pytest

import app.api.v1.scan as scan_module

_ID_RE = re.compile(r"^[0-9]{16}$")


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _capture_diagnostics(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake(**fields):
        calls.append(fields)

    monkeypatch.setattr(scan_module, "record_scan_diagnostic", _fake)
    return calls


# --- Success path -------------------------------------------------------


@pytest.mark.asyncio
async def test_client_supplied_attempt_id_is_echoed_on_success(app_client):
    headers = await _register_device(app_client, "attempt-echo-success")
    headers["X-Scan-Attempt-Id"] = "1234567890123456"
    headers["X-Scan-Request-Sequence"] = "1"

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999998"}, headers=headers
    )

    assert resp.headers["X-Scan-Attempt-Id"] == "1234567890123456"


@pytest.mark.asyncio
async def test_leading_zeros_round_trip_exactly(app_client):
    headers = await _register_device(app_client, "attempt-leading-zero")
    headers["X-Scan-Attempt-Id"] = "0000000000000007"

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999997"}, headers=headers
    )

    assert resp.headers["X-Scan-Attempt-Id"] == "0000000000000007"


@pytest.mark.asyncio
async def test_diagnostic_journal_carries_attempt_id_and_sequence(app_client, monkeypatch):
    calls = _capture_diagnostics(monkeypatch)
    headers = await _register_device(app_client, "attempt-journal")
    headers["X-Scan-Attempt-Id"] = "5555555555555555"
    headers["X-Scan-Request-Sequence"] = "3"

    await app_client.post("/api/v1/scan/barcode", json={"barcode": "9999999999996"}, headers=headers)

    assert len(calls) == 1
    assert calls[0]["scanAttemptId"] == "5555555555555555"
    assert calls[0]["requestSequence"] == 3
    assert calls[0]["origin"] == "backend"


# --- Missing / malformed headers ----------------------------------------


@pytest.mark.asyncio
async def test_missing_attempt_id_header_gets_a_generated_one(app_client):
    headers = await _register_device(app_client, "attempt-missing")

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999995"}, headers=headers
    )

    assert _ID_RE.match(resp.headers["X-Scan-Attempt-Id"])


@pytest.mark.asyncio
async def test_malformed_attempt_id_header_is_replaced_not_reflected(app_client):
    headers = await _register_device(app_client, "attempt-malformed")
    headers["X-Scan-Attempt-Id"] = "not-a-valid-id"

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999994"}, headers=headers
    )

    returned = resp.headers["X-Scan-Attempt-Id"]
    assert _ID_RE.match(returned)
    assert returned != "not-a-valid-id"


@pytest.mark.asyncio
async def test_missing_sequence_header_defaults_and_never_breaks_the_scan(app_client, monkeypatch):
    calls = _capture_diagnostics(monkeypatch)
    headers = await _register_device(app_client, "attempt-no-sequence")
    headers["X-Scan-Attempt-Id"] = "6666666666666666"

    resp = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": "9999999999993"}, headers=headers
    )

    assert resp.status_code == 404  # normal not-found handling, unaffected
    assert calls[0]["requestSequence"] == 1


# --- Header echoed on handled failure paths ------------------------------


@pytest.mark.asyncio
async def test_attempt_id_is_echoed_on_a_validation_failure(app_client):
    headers = await _register_device(app_client, "attempt-validation-fail")
    headers["X-Scan-Attempt-Id"] = "7777777777777777"

    resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": "  "}, headers=headers)

    assert resp.status_code == 422
    assert resp.headers["X-Scan-Attempt-Id"] == "7777777777777777"


@pytest.mark.asyncio
async def test_attempt_id_is_echoed_on_an_auth_failure(app_client):
    resp = await app_client.post(
        "/api/v1/scan/barcode",
        json={"barcode": "9999999999992"},
        headers={"X-Scan-Attempt-Id": "8888888888888888"},
    )

    assert resp.status_code == 401
    assert resp.headers["X-Scan-Attempt-Id"] == "8888888888888888"


@pytest.mark.asyncio
async def test_attempt_id_is_echoed_on_a_rate_limit_failure(app_client):
    headers = await _register_device(app_client, "attempt-rate-limit")
    headers["X-Scan-Attempt-Id"] = "9999999999999999"

    last_resp = None
    for _ in range(31):  # default RATE_LIMIT_SCAN_PER_HOUR is 30
        last_resp = await app_client.post(
            "/api/v1/scan/barcode", json={"barcode": "9999999999991"}, headers=headers
        )

    assert last_resp.status_code == 429
    assert last_resp.headers["X-Scan-Attempt-Id"] == "9999999999999999"


# --- Scoping: never applied outside the three scan endpoints -------------


@pytest.mark.asyncio
async def test_header_not_set_on_unrelated_endpoints(app_client):
    headers = await _register_device(app_client, "attempt-unrelated")

    resp = await app_client.get("/api/v1/scan-history", headers=headers)

    assert "X-Scan-Attempt-Id" not in resp.headers
