"""
Android <-> backend compatibility for scan-attempt diagnostics (issue #30),
driven against the REAL committed schema through the HTTP API with a real
journal file -- not against the documentation.

The Android client (not available in this repository when this test was
written) is specified as: a 16-digit ASCII `scanAttemptId` string generated
before acquisition; `X-Scan-Attempt-Id` + `X-Scan-Request-Sequence` on all
three scan endpoints; the SAME id with an incremented sequence on an
automatic auth retry; authenticated `POST /scan-diagnostics/client-events`
batches of at most 20 events / 16 KiB; removal from its outbox of only
`acceptedEventIds` and `duplicateEventIds` while keeping `retryableEventIds`;
Round 2 stages and PARTIAL / INTERRUPTED outcomes; and no photos, OCR text,
barcodes, credentials or exception messages in queued events.
"""
import io
import json
import uuid

import pytest
from PIL import Image

from app.core import client_event_ledger, scan_diagnostics
from app.integrations.gemini import gemini_service

ATTEMPT = "0123456789012345"  # leading zero: must stay a string end to end


@pytest.fixture(autouse=True)
def _journal(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "scan.jsonl"))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 16 * 1024 * 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    monkeypatch.setattr(client_event_ledger.settings, "CLIENT_EVENT_LEDGER_PATH", str(tmp_path / "ledger.sqlite3"))
    client_event_ledger.reset_for_testing()
    yield tmp_path / "scan.jsonl"
    client_event_ledger.reset_for_testing()


def _records(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


async def _device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(1, 2, 3)).save(buf, format="JPEG")
    return buf.getvalue()


def _event(seq: int, stage: str, outcome: str, **kw) -> dict:
    event = {
        "eventId": str(uuid.uuid4()), "scanAttemptId": ATTEMPT, "requestSequence": None, "sequence": seq,
        "occurredAt": "2026-10-06T10:00:00Z", "stage": stage, "outcome": outcome, "durationMs": 5,
        "appVersion": "1.4.0", "reasonCode": None, "metrics": None,
    }
    event.update(kw)
    return event


def _android_attempt_events() -> list[dict]:
    """A full attempt as Android would queue it, covering every Round 2
    stage and the PARTIAL / INTERRUPTED / CANCELLED outcomes."""
    return [
        _event(0, "ATTEMPT_START", "STARTED"),
        _event(1, "CAPTURE_COMPLETE", "SUCCEEDED"),
        _event(2, "IMAGE_PREPARATION", "SUCCEEDED", metrics={"imageWidthPx": 1920, "imageHeightPx": 1080, "imageBytes": 350000}),
        _event(3, "UPLOAD_START", "STARTED", requestSequence=1, metrics={"uploadBytes": 120000}),
        _event(4, "UPLOAD_RETRY", "RETRIED", requestSequence=1, reasonCode="AUTH_RETRY", metrics={"retryCount": 1}),
        _event(5, "UPLOAD_START", "STARTED", requestSequence=2),
        _event(6, "RESPONSE_RECEIVED", "SUCCEEDED", requestSequence=2),
        _event(7, "PARSING", "SUCCEEDED", requestSequence=2),
        _event(8, "PERSISTENCE", "SUCCEEDED", metrics={"outboxDepth": 3, "queuedMs": 40}),
        _event(9, "TERMINAL_SUCCESS", "PARTIAL", requestSequence=2),  # partial result, NOT a failure
        _event(10, "TERMINAL_FAILURE", "INTERRUPTED", reasonCode="UNKNOWN"),
        _event(11, "TERMINAL_CANCELLED", "CANCELLED", reasonCode="USER_CANCELLED"),
    ]


@pytest.mark.asyncio
async def test_all_three_endpoints_echo_the_same_attempt_id_and_sequence_retry_keeps_the_id(app_client, monkeypatch, _journal):
    async def fake_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps({"productName": "P", "rawIngredientText": "Water, Sugar", "ingredients": [],
                           "sugarGrams": 2.0, "sodiumMg": 80.0, "saturatedFatGrams": 0.5, "nutritionBasis": "PER_100_G"})

    monkeypatch.setattr(gemini_service, "analyze_image", fake_image)
    headers = await _device(app_client, "android-compat-endpoints")

    # automatic auth retry: first request has no/expired credentials (seq 1), retry carries them (seq 2), SAME id
    first = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar"},
        headers={"X-Scan-Attempt-Id": ATTEMPT, "X-Scan-Request-Sequence": "1"},
    )
    assert first.status_code == 401 and first.headers["X-Scan-Attempt-Id"] == ATTEMPT
    retry = await app_client.post(
        "/api/v1/scan/ocr-text", json={"rawText": "Water, Sugar"},
        headers={**headers, "X-Scan-Attempt-Id": ATTEMPT, "X-Scan-Request-Sequence": "2"},
    )
    assert retry.status_code == 200 and retry.headers["X-Scan-Attempt-Id"] == ATTEMPT

    image = await app_client.post(
        "/api/v1/scan/label-image", files={"image": ("l.jpg", _jpeg(), "image/jpeg")},
        headers={**headers, "X-Scan-Attempt-Id": ATTEMPT, "X-Scan-Request-Sequence": "1"},
    )
    assert image.status_code == 200 and image.headers["X-Scan-Attempt-Id"] == ATTEMPT
    barcode = await app_client.post(
        "/api/v1/scan/barcode", json={"barcode": image.json()["product"]["barcode"]},
        headers={**headers, "X-Scan-Attempt-Id": ATTEMPT, "X-Scan-Request-Sequence": "1"},
    )
    assert barcode.status_code == 200 and barcode.headers["X-Scan-Attempt-Id"] == ATTEMPT

    backend = [r for r in _records(_journal) if r.get("origin") == "backend" and r.get("scanAttemptId") == ATTEMPT]
    assert {r["operation"] for r in backend if "operation" in r} >= {"scan_ocr_text", "scan_label_image", "scan_barcode"}
    # internal pipeline stages are correlated to the SAME attempt id
    assert any("pipelineEvent" in r for r in backend)
    # the authenticated retry is journaled as sequence 2; the pre-auth 401 writes no journal line (documented gap)
    ocr = [r for r in backend if r.get("operation") == "scan_ocr_text" and "pipelineEvent" not in r]
    assert [r["requestSequence"] for r in ocr] == [2]


@pytest.mark.asyncio
async def test_android_outbox_flow_acks_dedup_owner_isolation_privacy_and_correlation(app_client, _journal):
    headers = await _device(app_client, "android-compat-a")
    events = _android_attempt_events()
    outbox = {e["eventId"]: e for e in events}

    # a leaked-field attempt: extra keys must never reach the journal
    leaky = _event(12, "PARSING", "FAILED", reasonCode="DECODE_ERROR")
    leaky.update({"barcode": "5449000000996", "ocrText": "SECRET-OCR-TEXT", "exceptionMessage": "SECRET-EXC", "imageBase64": "SECRET-IMG"})
    outbox[leaky["eventId"]] = leaky

    url = "/api/v1/scan-diagnostics/client-events"
    body = {"events": list(outbox.values())}
    assert len(json.dumps(body)) <= 16 * 1024 and len(body["events"]) <= 20
    resp = await app_client.post(url, json=body, headers=headers)
    assert resp.status_code == 200, resp.text
    ack = resp.json()
    assert set(ack["acceptedEventIds"]) == set(outbox) and ack["duplicateEventIds"] == [] and ack["retryableEventIds"] == []
    for event_id in ack["acceptedEventIds"] + ack["duplicateEventIds"]:  # Android removes ONLY these
        outbox.pop(event_id)
    assert outbox == {}

    # replay after a lost response: every id is a duplicate, none re-written
    before = len(_records(_journal))
    replay = await app_client.post(url, json=body, headers=headers)
    assert set(replay.json()["duplicateEventIds"]) == {e["eventId"] for e in body["events"]}
    assert replay.json()["acceptedEventIds"] == [] and len(_records(_journal)) == before

    android = [r for r in _records(_journal) if r.get("origin") == "android"]
    assert {r["stage"] for r in android} >= {
        "IMAGE_PREPARATION", "PARSING", "PERSISTENCE", "UPLOAD_RETRY", "TERMINAL_CANCELLED"}
    outcomes = {r["stage"] + ":" + r["outcome"] for r in android}
    assert "TERMINAL_SUCCESS:PARTIAL" in outcomes and "TERMINAL_FAILURE:INTERRUPTED" in outcomes
    assert not any(r["outcome"] == "FAILED" and r["stage"] == "TERMINAL_SUCCESS" for r in android)  # partial never mapped to failure
    assert all(r["scanAttemptId"] == ATTEMPT for r in android)
    text = _journal.read_text(encoding="utf-8")
    for secret in ("SECRET-OCR-TEXT", "SECRET-EXC", "SECRET-IMG", "5449000000996"):
        assert secret not in text

    # owner isolation: another device reusing the same eventId is NOT a duplicate and is journaled under its own scope
    other = await _device(app_client, "android-compat-b")
    shared = body["events"][0]
    other_resp = await app_client.post(url, json={"events": [shared]}, headers=other)
    assert other_resp.json()["acceptedEventIds"] == [shared["eventId"]]
    scopes = {r["ownerScope"] for r in _records(_journal) if r.get("eventId") == shared["eventId"]}
    assert len(scopes) == 2


@pytest.mark.asyncio
async def test_retryable_events_stay_in_the_outbox_when_the_journal_is_unavailable(app_client, monkeypatch):
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", False)
    headers = await _device(app_client, "android-compat-disabled")
    events = _android_attempt_events()[:3]
    ack = (await app_client.post("/api/v1/scan-diagnostics/client-events", json={"events": events}, headers=headers)).json()
    assert ack["acceptedEventIds"] == [] and set(ack["retryableEventIds"]) == {e["eventId"] for e in events}


@pytest.mark.asyncio
async def test_batch_bounds_20_events_ok_21_rejected_and_worst_case_20_fits_16_kib(app_client):
    headers = await _device(app_client, "android-compat-bounds")
    worst = [
        _event(i, "UPLOAD_RETRY", "RETRIED", requestSequence=999_999_999, reasonCode="NETWORK_TIMEOUT",
               appVersion="a" * 32, durationMs=86_400_000,
               metrics={"imageWidthPx": 20000, "imageHeightPx": 20000, "imageBytes": 104857600, "uploadBytes": 104857600,
                        "retryCount": 1000, "ocrConfidencePct": 100, "outboxDepth": 10000, "queuedMs": 86_400_000})
        for i in range(20)
    ]
    assert len(json.dumps({"events": worst})) <= 16 * 1024  # Android's worst-case batch is under the server cap
    ok = await app_client.post("/api/v1/scan-diagnostics/client-events", json={"events": worst}, headers=headers)
    assert ok.status_code == 200 and len(ok.json()["acceptedEventIds"]) == 20
    too_many = await app_client.post(
        "/api/v1/scan-diagnostics/client-events", json={"events": worst + [_event(99, "ATTEMPT_START", "STARTED")]}, headers=headers
    )
    assert too_many.status_code == 422
