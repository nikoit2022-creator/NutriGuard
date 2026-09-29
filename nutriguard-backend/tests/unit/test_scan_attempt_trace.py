import json

import pytest

from app.seed import scan_attempt_trace as trace


def _write_lines(path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


def test_rejects_a_malformed_scan_attempt_id():
    with pytest.raises(ValueError):
        trace.find_attempt("not-16-digits")


def test_no_journal_file_returns_zero_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(tmp_path / "missing.jsonl"))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

    report = trace.find_attempt("1" * 16)

    assert report["matchCount"] == 0
    assert report["events"] == []
    assert all(not f["existed"] for f in report["filesChecked"])


def test_finds_matches_across_current_and_backup_files(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    backup = tmp_path / "scan.jsonl.1"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

    _write_lines(
        backup,
        {
            "timestamp": "2026-09-29T10:00:00Z",
            "origin": "backend",
            "scanAttemptId": "1" * 16,
            "requestId": "req-1",
            "requestSequence": 1,
            "stage": "response_built",
            "outcome": "success",
        },
    )
    _write_lines(
        path,
        {
            "timestamp": "2026-09-29T10:00:05Z",
            "origin": "android",
            "scanAttemptId": "1" * 16,
            "eventId": "evt-1",
            "requestSequence": 1,
            "stage": "TERMINAL_SUCCESS",
            "outcome": "SUCCEEDED",
        },
        {
            "timestamp": "2026-09-29T09:59:59Z",  # out of insertion order
            "origin": "android",
            "scanAttemptId": "1" * 16,
            "eventId": "evt-0",
            "requestSequence": None,
            "stage": "ATTEMPT_START",
            "outcome": "STARTED",
        },
        {
            "timestamp": "2026-09-29T10:00:10Z",
            "origin": "backend",
            "scanAttemptId": "9" * 16,  # different attempt -- must not match
            "requestId": "req-other",
            "requestSequence": 1,
            "stage": "response_built",
            "outcome": "success",
        },
    )

    report = trace.find_attempt("1" * 16)

    assert report["matchCount"] == 3
    assert report["origins"] == ["android", "backend"]
    # Chronological by server timestamp, not insertion/file order.
    assert [e["eventId"] if "eventId" in e else e["requestId"] for e in report["events"]] == [
        "evt-0",
        "req-1",
        "evt-1",
    ]
    assert report["ambiguousCollision"] is False


def test_request_id_filter_narrows_results(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {"timestamp": "t1", "origin": "backend", "scanAttemptId": "2" * 16, "requestId": "req-a"},
        {"timestamp": "t2", "origin": "backend", "scanAttemptId": "2" * 16, "requestId": "req-b"},
    )

    report = trace.find_attempt("2" * 16, request_id="req-a")

    assert report["matchCount"] == 1
    assert report["events"][0]["requestId"] == "req-a"


def test_malformed_lines_are_skipped_and_counted(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
        f.write(json.dumps({"timestamp": "t1", "origin": "backend", "scanAttemptId": "3" * 16}) + "\n")

    report = trace.find_attempt("3" * 16)

    assert report["matchCount"] == 1
    assert report["malformedLineCount"] == 1


def test_flags_ambiguous_collision_on_two_first_requests(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {
            "timestamp": "t1",
            "origin": "backend",
            "scanAttemptId": "4" * 16,
            "requestId": "req-x",
            "requestSequence": 1,
        },
        {
            "timestamp": "t2",
            "origin": "backend",
            "scanAttemptId": "4" * 16,
            "requestId": "req-y",
            "requestSequence": 1,
        },
    )

    report = trace.find_attempt("4" * 16)

    assert report["ambiguousCollision"] is True
    assert report["ambiguousCollisionNote"] is not None


def test_human_readable_output_does_not_raise(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {"timestamp": "t1", "origin": "backend", "scanAttemptId": "5" * 16, "requestId": "req-z"},
    )

    report = trace.find_attempt("5" * 16)
    rendered = trace._human_readable(report)

    assert "5555555555555555" in rendered
    assert "req-z" in rendered
