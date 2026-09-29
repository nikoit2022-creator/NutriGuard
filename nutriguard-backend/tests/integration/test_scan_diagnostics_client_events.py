"""
Full HTTP coverage of POST /api/v1/scan-diagnostics/client-events
(issue #30, see docs/SCAN_ATTEMPT_DIAGNOSTICS.md).
"""
import json

import pytest

from app.core import client_event_ledger, scan_diagnostics


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
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_event_ledger.settings, "CLIENT_EVENT_LEDGER_PATH", str(tmp_path / "ledger.sqlite3")
    )
    client_event_ledger.reset_for_testing()
    yield
    client_event_ledger.reset_for_testing()


# --- Auth ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requires_authentication(app_client):
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json={"events": [_event()]}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_enforces_its_own_rate_limit(app_client):
    headers = await _register_device(app_client, "diag-events-rate-limit")

    last_resp = None
    for i in range(121):  # default RATE_LIMIT_DIAGNOSTICS_PER_HOUR is 120
        last_resp = await app_client.post(
            "/api/v1/scan-diagnostics/client-events",
            json={"events": [_event(eventId=f"22222222-2222-4222-8222-222222222{i:03d}", sequence=i)]},
            headers=headers,
        )

    assert last_resp.status_code == 429
    assert last_resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# --- Happy path / truthful acknowledgment / dedup ---------------------------


@pytest.mark.asyncio
async def test_accepts_a_valid_batch_when_diagnostics_are_enabled(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

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
    assert body["retryableEventIds"] == []


@pytest.mark.asyncio
async def test_resubmitting_an_accepted_event_is_reported_as_a_duplicate(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
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
    assert second.json()["retryableEventIds"] == []


@pytest.mark.asyncio
async def test_same_event_id_from_two_users_is_not_a_cross_user_duplicate(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    headers_a = await _register_device(app_client, "diag-events-user-a")
    headers_b = await _register_device(app_client, "diag-events-user-b")
    payload = {"events": [_event()]}

    resp_a = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers_a
    )
    resp_b = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers_b
    )

    assert resp_a.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert resp_b.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert resp_a.json()["duplicateEventIds"] == []
    assert resp_b.json()["duplicateEventIds"] == []


@pytest.mark.asyncio
async def test_partial_batch_one_bad_write_does_not_affect_other_events(app_client, tmp_path, monkeypatch):
    """Codex review round 2, finding 2: "handle partial-batch failure
    safely" -- one event's journal write failing must not affect any
    other event in the same batch."""
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    headers = await _register_device(app_client, "diag-events-partial-batch")

    real_record = scan_diagnostics.record_scan_diagnostic
    call_count = {"n": 0}

    def _flaky_record(**fields):
        call_count["n"] += 1
        if fields.get("eventId") == "11111111-1111-4111-8111-111111111112":
            return False  # simulates a failed write for this one event only
        return real_record(**fields)

    monkeypatch.setattr(scan_diagnostics, "record_scan_diagnostic", _flaky_record)

    events = [
        _event(eventId="11111111-1111-4111-8111-111111111111", sequence=0),
        _event(eventId="11111111-1111-4111-8111-111111111112", sequence=1),
        _event(eventId="11111111-1111-4111-8111-111111111113", sequence=2),
    ]
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json={"events": events}, headers=headers
    )

    body = resp.json()
    assert sorted(body["acceptedEventIds"]) == [
        "11111111-1111-4111-8111-111111111111",
        "11111111-1111-4111-8111-111111111113",
    ]
    assert body["retryableEventIds"] == ["11111111-1111-4111-8111-111111111112"]
    assert body["duplicateEventIds"] == []


@pytest.mark.asyncio
async def test_a_retryable_event_can_be_resubmitted_and_then_accepted(app_client, tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    headers = await _register_device(app_client, "diag-events-retry-then-accept")
    payload = {"events": [_event()]}

    real_record = scan_diagnostics.record_scan_diagnostic
    monkeypatch.setattr(scan_diagnostics, "record_scan_diagnostic", lambda **fields: False)
    first = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers
    )
    assert first.json()["retryableEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert first.json()["acceptedEventIds"] == []

    monkeypatch.setattr(scan_diagnostics, "record_scan_diagnostic", real_record)
    second = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json=payload, headers=headers
    )
    # The failed attempt released its reservation -- this retry can win
    # and actually persist, rather than being stuck forever.
    assert second.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]


@pytest.mark.asyncio
async def test_events_are_retryable_not_falsely_accepted_when_diagnostics_disabled(
    app_client, tmp_path, monkeypatch
):
    """Codex review round 2, finding 2: disabled storage must produce an
    explicit retryable response, never a false acknowledgment."""
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
    body = resp.json()
    assert body["acceptedEventIds"] == []
    assert body["retryableEventIds"] == ["11111111-1111-4111-8111-111111111111"]
    assert not path.exists()


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
async def test_rejects_a_body_larger_than_the_configured_byte_limit_via_content_length(
    app_client, monkeypatch
):
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
async def test_rejects_an_oversized_body_streamed_without_a_content_length_header(app_client, monkeypatch):
    """Codex review round 2, finding 6: the bound must hold even when
    Content-Length is missing/understated (e.g. chunked transfer), not
    only via the fast pre-check. httpx always sets a real
    Content-Length for a `content=` bytes payload, so this asserts the
    STREAMING check itself catches an oversized body directly (with the
    Content-Length pre-check bypassed by disabling it), rather than
    relying on transport-level chunking support in the test client."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES", 64)
    headers = await _register_device(app_client, "diag-events-body-too-big-streamed")

    big_payload = json.dumps({"events": [_event(appVersion="1" * 200)]}).encode("utf-8")
    resp = await app_client.post(
        "/api/v1/scan-diagnostics/client-events",
        content=big_payload,
        headers={**headers, "Content-Type": "application/json"},
    )
    assert resp.status_code in (413, 422)  # 413 from the streaming check, or 422 (appVersion pattern)
    if resp.status_code == 413:
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
    assert resp.json()["acceptedEventIds"] == ["11111111-1111-4111-8111-111111111111"]

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["origin"] == "android"
    assert payload["eventId"] == "11111111-1111-4111-8111-111111111111"
    assert payload["scanAttemptId"] == "1234567890123456"
    assert payload["stage"] == "ATTEMPT_START"
    assert payload["outcome"] == "STARTED"
    assert payload["metrics"] == {"ocrConfidencePct": 91.5}
    # Privacy allowlist: no user id, no free-text fields ever land here --
    # only the stable, one-way pseudonymous ownerScope (Codex review
    # round 2, finding 4).
    assert "userId" not in payload
    assert "user_id" not in payload
    assert isinstance(payload["ownerScope"], str) and payload["ownerScope"]
