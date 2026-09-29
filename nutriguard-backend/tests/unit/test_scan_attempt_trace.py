import json
import multiprocessing as mp

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


def test_non_object_json_lines_are_malformed_not_a_crash(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(["not", "an", "object"]) + "\n")
        f.write(json.dumps("also not an object") + "\n")
        f.write(json.dumps(42) + "\n")
        f.write(json.dumps({"timestamp": "t1", "origin": "backend", "scanAttemptId": "6" * 16}) + "\n")

    report = trace.find_attempt("6" * 16)

    assert report["matchCount"] == 1
    assert report["malformedLineCount"] == 3


def test_invalid_field_types_do_not_crash_sorting_or_grouping(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {"timestamp": 12345, "origin": "backend", "scanAttemptId": "7" * 16, "requestId": ["not-a-string"]},
        {"timestamp": "t2", "origin": ["not-a-string-either"], "scanAttemptId": "7" * 16},
        {"timestamp": "t3", "origin": "android", "scanAttemptId": "7" * 16, "eventId": "evt-ok"},
    )

    report = trace.find_attempt("7" * 16)  # must not raise

    assert report["matchCount"] == 3
    assert "android" in report["origins"]


def test_unreadable_file_is_reported_not_raised(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    path.chmod(0o000)
    try:
        report = trace.find_attempt("8" * 16)  # must not raise
    finally:
        path.chmod(0o644)

    assert any(f.get("unreadable") for f in report["filesChecked"])


def test_reading_never_creates_a_per_rotated_file_lock(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    backup = tmp_path / "scan.jsonl.1"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)
    _write_lines(backup, {"timestamp": "t1", "origin": "backend", "scanAttemptId": "9" * 16})
    _write_lines(path, {"timestamp": "t2", "origin": "backend", "scanAttemptId": "9" * 16})

    trace.find_attempt("9" * 16)

    assert not (tmp_path / "scan.jsonl.1.lock").exists()
    assert (tmp_path / "scan.jsonl.lock").exists()


def test_reports_lock_acquired_true_under_normal_conditions(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(path, {"timestamp": "t1", "origin": "backend", "scanAttemptId": "1" * 16})

    report = trace.find_attempt("1" * 16)

    assert report["lockAcquired"] is True


def test_definite_owner_collision_across_two_distinct_owner_scopes(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {
            "timestamp": "t1",
            "origin": "android",
            "scanAttemptId": "2" * 16,
            "eventId": "evt-owner-a",
            "ownerScope": "owner-scope-a",
        },
        {
            "timestamp": "t2",
            "origin": "android",
            "scanAttemptId": "2" * 16,
            "eventId": "evt-owner-b",
            "ownerScope": "owner-scope-b",
        },
    )

    report = trace.find_attempt("2" * 16)

    assert report["ambiguousCollision"] is True
    assert len(report["ownerScopes"]) == 2
    assert "DIFFERENT owner scopes" in report["ambiguousCollisionNote"]


def test_same_owner_scope_is_not_flagged_as_a_collision(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {
            "timestamp": "t1",
            "origin": "android",
            "scanAttemptId": "3" * 16,
            "eventId": "evt-1",
            "ownerScope": "owner-scope-a",
        },
        {
            "timestamp": "t2",
            "origin": "android",
            "scanAttemptId": "3" * 16,
            "eventId": "evt-2",
            "ownerScope": "owner-scope-a",
        },
    )

    report = trace.find_attempt("3" * 16)

    assert report["ambiguousCollision"] is False
    assert report["ownerScopes"] == ["owner-scope-a"]


def test_unknown_owner_scope_present_flag_is_explicit(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(trace.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 0)
    _write_lines(
        path,
        {"timestamp": "t1", "origin": "backend", "scanAttemptId": "4" * 16, "requestId": "req-legacy"},
    )

    report = trace.find_attempt("4" * 16)

    assert report["unknownOwnerScopePresent"] is True
    assert report["ownerScopes"] == []


def _mp_writer_forcing_rotation(path_str: str, max_bytes: int, backup_count: int, n_lines: int, stop_after) -> None:
    from app.core import scan_diagnostics as sd

    sd.settings.SCAN_DIAGNOSTICS_ENABLED = True
    sd.settings.SCAN_DIAGNOSTICS_PATH = path_str
    sd.settings.SCAN_DIAGNOSTICS_MAX_BYTES = max_bytes
    sd.settings.SCAN_DIAGNOSTICS_BACKUP_COUNT = backup_count
    for i in range(n_lines):
        sd.record_scan_diagnostic(
            requestId=f"req-{i}", origin="backend", scanAttemptId="9" * 16, requestSequence=1
        )
    stop_after.set()


def _mp_reader_during_rotation(path_str: str, backup_count: int, stop_after, error_path: str) -> None:
    import time as _time

    from app.core.config import settings as app_settings

    app_settings.SCAN_DIAGNOSTICS_PATH = path_str
    app_settings.SCAN_DIAGNOSTICS_BACKUP_COUNT = backup_count
    errors = []
    deadline = _time.monotonic() + 10
    while not stop_after.is_set() and _time.monotonic() < deadline:
        try:
            report = trace.find_attempt("9" * 16)
            assert report["matchCount"] == len(report["events"])
            for event in report["events"]:
                assert event.get("scanAttemptId") == "9" * 16
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))
            break
    with open(error_path, "w") as f:
        f.write("\n".join(errors))


def test_concurrent_rotation_does_not_corrupt_a_concurrent_read(tmp_path):
    path = tmp_path / "scan.jsonl"
    error_path = tmp_path / "reader_errors.txt"
    ctx = mp.get_context("fork")
    stop_after = ctx.Event()
    writer = ctx.Process(
        target=_mp_writer_forcing_rotation, args=(str(path), 200, 1, 400, stop_after)
    )
    reader = ctx.Process(
        target=_mp_reader_during_rotation, args=(str(path), 1, stop_after, str(error_path))
    )
    reader.start()
    writer.start()
    writer.join(timeout=30)
    stop_after.set()
    reader.join(timeout=15)

    assert writer.exitcode == 0
    assert reader.exitcode == 0
    errors = error_path.read_text() if error_path.exists() else ""
    assert not errors.strip(), f"reader observed an error during concurrent rotation: {errors}"
    assert not (tmp_path / "scan.jsonl.1.lock").exists()


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
