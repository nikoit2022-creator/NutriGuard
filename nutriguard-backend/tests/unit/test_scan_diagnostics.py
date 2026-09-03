import json
import multiprocessing as mp

from app.core import scan_diagnostics


def test_scan_diagnostic_is_bounded_and_contains_no_payload(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 1024)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

    scan_diagnostics.record_scan_diagnostic(
        requestId="request-1",
        barcode="3800123456789",
        outcome="partial",
        errorCode="PRODUCT_NOT_FOUND",
        recognizedIngredientCount=4,
    )

    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload["barcode"] == "3800123456789"
    assert payload["outcome"] == "partial"
    assert payload["recognizedIngredientCount"] == 4
    assert "image" not in payload
    assert "rawText" not in payload


def test_scan_diagnostic_disabled_writes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", False)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))

    scan_diagnostics.record_scan_diagnostic(barcode="3800123456789", outcome="success")

    assert not path.exists()


def test_scan_diagnostic_rotates_when_over_the_configured_limit(tmp_path, monkeypatch):
    path = tmp_path / "scan.jsonl"
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(path))
    # Small enough that a handful of lines forces a rotation.
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_MAX_BYTES", 200)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_BACKUP_COUNT", 1)

    for i in range(20):
        scan_diagnostics.record_scan_diagnostic(barcode=f"38001234567{i:02d}", outcome="success")

    backup = path.with_name(path.name + ".1")
    assert backup.exists()
    # Bounded: current file plus exactly one backup, neither far past the limit.
    assert path.stat().st_size <= 200 + 512
    assert backup.stat().st_size <= 200 + 512
    assert not path.with_name(path.name + ".2").exists()

    # Every surviving line (current file + the one backup) is still valid,
    # complete, standalone JSON -- rotation never truncates a line.
    for candidate in (path, backup):
        for raw_line in candidate.read_text(encoding="utf-8").splitlines():
            json.loads(raw_line)


def test_scan_diagnostic_write_error_never_raises(tmp_path, monkeypatch):
    # An unwritable path (parent is a file, not a directory) must not
    # propagate -- a scan must never fail because diagnostics couldn't write.
    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x")
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_ENABLED", True)
    monkeypatch.setattr(scan_diagnostics.settings, "SCAN_DIAGNOSTICS_PATH", str(blocker / "scan.jsonl"))

    scan_diagnostics.record_scan_diagnostic(barcode="3800123456789", outcome="success")  # must not raise


# --- Multi-process safety --------------------------------------------------
#
# Production runs multiple Uvicorn *worker processes* sharing one log file
# on one mounted volume (`--workers 4`, see docker-compose.prod.yml). These
# helpers run in genuinely separate OS processes (`multiprocessing`, `fork`
# context) -- not threads -- so they exercise the real cross-process race
# `_append_locked`'s `fcntl.flock` closes, the same class of race a
# thread-only lock (or the stdlib `RotatingFileHandler`) cannot.


def _mp_worker_write(
    path_str: str, max_bytes: int, backup_count: int, worker_id: int, count: int, barrier, stderr_path: str
) -> None:
    import os
    import sys

    # Redirect this process's own stderr fd to a per-worker file so the
    # test can see an internal exception (e.g. a racing `os.rename`
    # during rotation) even when something upstream swallows it instead
    # of letting it propagate -- this is exactly how the old
    # `RotatingFileHandler`-based implementation failed: `Handler.emit`
    # catches its own exceptions and only *prints* them via
    # `logging.Handler.handleError`, so a crash there never raised, never
    # touched `record_scan_diagnostic`'s own try/except, and never
    # changed this process's exit code -- only stderr showed it.
    stderr_file = open(stderr_path, "w")
    os.dup2(stderr_file.fileno(), sys.stderr.fileno())

    scan_diagnostics.settings.SCAN_DIAGNOSTICS_ENABLED = True
    scan_diagnostics.settings.SCAN_DIAGNOSTICS_PATH = path_str
    scan_diagnostics.settings.SCAN_DIAGNOSTICS_MAX_BYTES = max_bytes
    scan_diagnostics.settings.SCAN_DIAGNOSTICS_BACKUP_COUNT = backup_count
    barrier.wait()  # start every worker at (as close as possible to) the same instant
    for i in range(count):
        scan_diagnostics.record_scan_diagnostic(
            requestId=f"w{worker_id}-{i}",
            barcode="3800123456789",
            outcome="success",
            workerId=worker_id,
        )


def _mp_worker_write_broken(broken_path_str: str, barrier) -> None:
    scan_diagnostics.settings.SCAN_DIAGNOSTICS_ENABLED = True
    scan_diagnostics.settings.SCAN_DIAGNOSTICS_PATH = broken_path_str
    barrier.wait()
    # The parent directory is actually a file -- every write must fail --
    # yet this must exit 0 (must never raise / never touch the scan flow).
    for _ in range(10):
        scan_diagnostics.record_scan_diagnostic(barcode="3800123456789", outcome="success")


def test_scan_diagnostic_is_safe_across_concurrent_processes(tmp_path):
    path = tmp_path / "scan.jsonl"
    # Deliberately tiny relative to the total volume written below, so
    # rotation fires on nearly every line -- the specific race this fix
    # targets (a worker mid-append while another worker rotates the file
    # out from under it) needs rotation to actually be contended, not rare.
    max_bytes = 150
    backup_count = 1
    n_workers = 16
    n_per_worker = 150

    blocker = tmp_path / "not_a_directory"
    blocker.write_text("x")

    ctx = mp.get_context("fork")
    barrier = ctx.Barrier(n_workers + 1)  # +1 for the broken-path worker below
    stderr_paths = [tmp_path / f"worker_{w}.stderr" for w in range(n_workers)]
    procs = [
        ctx.Process(
            target=_mp_worker_write,
            args=(str(path), max_bytes, backup_count, w, n_per_worker, barrier, str(stderr_paths[w])),
        )
        for w in range(n_workers)
    ]
    # Runs concurrently with the real writers, deliberately pointed at an
    # unwritable path -- its failure must never affect the others' file
    # or the worker process itself (no crash, no propagated exception).
    procs.append(ctx.Process(target=_mp_worker_write_broken, args=(str(blocker / "scan.jsonl"), barrier)))

    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    for p in procs:
        assert p.exitcode == 0, f"a diagnostics worker process crashed: exitcode={p.exitcode}"

    # No worker's diagnostic write ever hit an internal exception (e.g. a
    # racing `os.rename` between two processes rotating the same file at
    # once) -- not even one silently printed and swallowed. This is the
    # direct signal for the exact race this fix removes: reproduced
    # against the pre-fix implementation, this loop reliably finds
    # `FileNotFoundError` tracebacks from `logging.handlers.rotate`.
    for stderr_path in stderr_paths:
        content = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
        assert not content.strip(), f"{stderr_path.name} captured an internal error:\n{content}"

    backup = path.with_name(path.name + ".1")
    assert backup.exists(), "expected at least one rotation to have happened"
    # Bounded: current file + exactly one backup, no unbounded growth and
    # no third generation ever kept around.
    assert not path.with_name(path.name + ".2").exists()
    assert path.stat().st_size <= max_bytes + 1024
    assert backup.stat().st_size <= max_bytes + 1024

    seen_request_ids: set[str] = set()
    total_lines = 0
    for f in (path, backup):
        raw = f.read_text(encoding="utf-8")
        # Every byte written belongs to a complete, newline-terminated
        # line -- no partial line ever left dangling by a race between a
        # writer and a concurrent rotation.
        assert raw == "" or raw.endswith("\n")
        for raw_line in raw.splitlines():
            # Fails with an exception if a line was truncated or two
            # writers' lines were merged into one -- neither ever happens
            # under the lock.
            payload = json.loads(raw_line)
            assert payload["barcode"] == "3800123456789"
            assert payload["outcome"] == "success"
            request_id = payload["requestId"]
            assert request_id not in seen_request_ids, f"duplicate/merged line for {request_id}"
            seen_request_ids.add(request_id)
            total_lines += 1

    # Older, rotated-away generations are legitimately dropped (that's the
    # bounded-size guarantee) -- but every survivor must be one that was
    # actually sent, one worker's line was never mixed into another's.
    all_sent_ids = {f"w{w}-{i}" for w in range(n_workers) for i in range(n_per_worker)}
    assert seen_request_ids <= all_sent_ids
    assert total_lines == len(seen_request_ids)
    assert total_lines > 0
