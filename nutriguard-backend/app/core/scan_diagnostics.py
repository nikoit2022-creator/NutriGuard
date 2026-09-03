"""Small opt-in diagnostic journal for temporary product-scan testing.

One compact JSON line is written per image scan. Images, OCR text, model
responses, credentials, user IDs and health-profile data are deliberately
excluded. Rotation is bounded by configuration so diagnostics cannot grow
without limit.

Multi-process safety: production runs this behind multiple Uvicorn worker
*processes* (`--workers 4`, see `docker-compose.prod.yml`) that all share
one log file on one mounted volume. `logging.handlers.RotatingFileHandler`
is only safe within a single process -- each worker would track the file's
size from its own writes alone, so one worker can keep appending past a
rotation another worker already performed (writing into a stale, renamed
file handle) and two workers can run `doRollover()` at the same time and
corrupt the rotation chain. Calling `handler.emit()` directly (bypassing
`Handler.handle()`) also skips even the single-process thread lock.
Instead, every append here is a single critical section -- read the
REAL on-disk size, rotate if needed, write one line -- serialized across
ALL processes with a POSIX advisory lock (`fcntl.flock`) on a sibling
lock file. Only one process/thread can be inside that section at a time,
so a line is never split, merged with another, or lost to a race with
rotation. `fcntl` is POSIX-only stdlib (no new dependency); it is
imported lazily inside the write path so importing this module -- or
running with diagnostics disabled -- never breaks a non-POSIX host.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings


def _rotate_locked(path: Path, backup_count: int) -> None:
    """Rename path -> path.1 -> path.2 -> ... up to `backup_count`, dropping
    the oldest. Must only be called while holding the cross-process lock.
    """
    if backup_count <= 0:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    for index in range(backup_count - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        if src.exists():
            dst.unlink(missing_ok=True)
            src.rename(dst)
    dst = path.with_name(f"{path.name}.1")
    dst.unlink(missing_ok=True)
    if path.exists():
        path.rename(dst)


def _append_locked(path: Path, line: bytes, max_bytes: int, backup_count: int) -> None:
    """Append one already-encoded line under an exclusive, cross-process
    lock, rotating first if the real on-disk file is already at/over the
    configured limit. The lock is released automatically when
    `lock_file` is closed (POSIX `flock` locks are tied to the open file
    description, not the process), so this is safe even if a worker is
    killed mid-write.
    """
    import fcntl  # POSIX-only; deferred so import-time never breaks Windows.

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            try:
                current_size = path.stat().st_size
            except FileNotFoundError:
                current_size = 0
            if current_size > 0 and current_size + len(line) > max_bytes:
                _rotate_locked(path, backup_count)
            with open(path, "ab") as f:
                f.write(line)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def record_scan_diagnostic(**fields: Any) -> None:
    """Best-effort write; diagnostic I/O must never break a scan."""
    try:
        if not settings.SCAN_DIAGNOSTICS_ENABLED:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backendVersion": settings.APP_VERSION,
            "pid": os.getpid(),
            **{key: value for key, value in fields.items() if value is not None},
        }
        line = (json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
        _append_locked(
            Path(settings.SCAN_DIAGNOSTICS_PATH),
            line,
            settings.SCAN_DIAGNOSTICS_MAX_BYTES,
            settings.SCAN_DIAGNOSTICS_BACKUP_COUNT,
        )
    except Exception:
        # This journal is observational only. Normal application logging
        # remains responsible for reporting operational failures.
        return
