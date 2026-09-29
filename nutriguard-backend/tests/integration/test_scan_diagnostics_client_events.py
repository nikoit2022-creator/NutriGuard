"""
Full HTTP coverage of POST /api/v1/scan-diagnostics/client-events
(issue #30, see docs/SCAN_ATTEMPT_DIAGNOSTICS.md).
"""
import json

import pytest

from app.core import scan_diagnostics


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


def _event(**overrides) -> dict:
    event = {
        "eventId": "11111111-1111-4111-8111-111111111111",
        "scanAttemptId": "1234567890123456",
        "requestSequence": 1,
        "sequence": 0,
        "occurredAt": "2026-09-29T10:00:00Z",
        "stage": "ATTEMPT_START",
        "outcome": "STARTED",
        "durationMs": 12,
        "appVersion": "1.4.0",
        "reasonCode": None,
        "metrics": None,
    }
    event.update(overrides)
    return event


@pytest.fixture(autouse=True)
def _reset_dedup_cache():
    scan_diagnostics.reset_client_event_dedup_cache()
    yield
    scan_diagnostics.reset_client_event_dedup_cache()


# --- Auth ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_authentication(app_client):
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json={"events": [_event()]}
    )
    assert resp.status_code == 401


# --- Happy path / dedup ------------------------------------------------------


@pytest.mark.asyncio
async def test_accepts_a_valid_batch(app_client):
    headers = await _register_device(app_client, "diag-events-accept")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event()]},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert body["duplicateEventIds"] == []


@pytest.mark.asyncio
async def test_resubmitting_the_same_event_is_reported_as_a_duplicate(app_client):
    headers = await _register_device(app_client, "diag-events-dup")
    payload = {"events": [_event()]}

    first = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers
    )
    second = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers
    )

    assert first.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert second.json()["acceptedEventIds"] == []
    assert second.json()["duplicateEventIds"] == ["11111111-1111-4111-8111-111111111111"]


@pytest.mark.asyncio
async def test_same_event_id_from_two_users_is_not_a_cross_user_duplicate(app_client):
    headers_a = await _register_device(app_client, "diag-events-user-a")
    headers_b = await _register_device(app_client, "diag-events-user-b")
    payload = {"events": [_event()]}

    resp_a = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers_a
    )
    resp_b = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers_b
    )

    assert resp_a.json()["duplicateEventIds"] == []
    assert resp_b.json()["duplicateEventIds"] == []


# --- Bounds ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_a_batch_larger_than_the_configured_max(app_client):
    headers = await _register_device(app_client, "diag-events-batch-too-big")
    events = [
        _event(eventId=f"11111111-1111-4111-8111-1111111111{i:02d}", sequence=i) for i in range(21)
    ]
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": events},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_rejects_a_body_larger_than_the_configured_byte_limit(app_client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 64)
    headers = await _register_device(app_client, "diag-events-body-too-big")

    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event()]},
        headers=headers,
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_rejects_a_malformed_scan_attempt_id(app_client):
    headers = await _register_device(app_client, "diag-events-bad-attempt-id")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event(scanAttemptId="short")]},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_an_unknown_metrics_key(app_client):
    headers = await _register_device(app_client, "diag-events-bad-metric-key")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event(metrics={"arbitraryFreeText": 1})]},
        headers=headers,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_rejects_a_metric_value_out_of_bounds(app_client):
    headers = await _register_device(app_client, "diag-events-metric-oob")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event(metrics={"ocrConfidencePct": 250})]},
        headers=headers,
    )
    assert resp.status_code == 422


# --- Storage: written to the shared bounded journal, origin-tagged ----------


@pytest.mark.asyncio
async def test_accepted_events_are_written_to_the_bounded_journal(app_client, tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

    headers = await _register_device(app_client, "diag-events-journal")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event(metrics={"ocrConfidencePct": 91.5})]},
        headers=headers,
    )
    assert resp.status_code == 200

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["origin"] == "android"
    assert payload["eventId"] == "11111111-1111-4111-8111-111111111111"
    assert payload["scanAttemptId"] == "1234567890123456"
    assert payload["stage"] == "ATTEMPT_START"
    assert payload["outcome"] == "STARTED"
    assert payload["metrics"] == {"ocrConfidencePct": 91.5}
    # Privacy allowlist: no user id, no free-text fields ever land here.
    assert "userId" not in payload
    assert "user_id" not in payload


@pytest.mark.asyncio
async def test_events_are_acknowledged_but_not_persisted_when_diagnostics_disabled(
    app_client, tmp_path, monkeypatch
):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", False)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))

    headers = await _register_device(app_client, "diag-events-disabled")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        json={"events": [_event()]},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert not path.exists()
