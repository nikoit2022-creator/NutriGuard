"""
Read-only operator CLI: look up one scan-attempt id across the bounded
scan-diagnostics journal (`app.core.scan_diagnostics`) and its rotated
backup file(s) (issue #30, see docs/SCAN_ATTEMPT_DIAGNOSTICS.md
section 7). Never writes, deletes, or otherwise mutates anything --
a plain reader, safe to run against a live, actively-written journal
(it takes the same sibling `.lock` file writers use, in SHARED mode,
so a read never observes a line mid-write -- see `_read_lines_safely`).

    python -m app.seed.scan_attempt_trace 0042135790246813
    python -m app.seed.scan_attempt_trace 0042135790246813 --human
    python -m app.seed.scan_attempt_trace 0042135790246813 --request-id <uuid>

Placed alongside the other operator/maintenance CLIs in `app/seed/`
(see `ingredient_candidate_maintenance.py`) even though it never
touches the database -- this is diagnostics-file tooling, not seeding.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.core.config import settings

_ATTEMPT_ID_PATTERN = re.compile(r"^[0-9]{16}$")


def _candidate_files(base_path: Path, backup_count: int) -> list[Path]:
    files = [base_path]
    for index in range(1, max(backup_count, 0) + 1):
        files.append(base_path.with_name(base_path.name + f".{index}"))
    return files


def _read_lines_safely(path: Path) -> tuple[list[dict], int, bool]:
    """Returns (parsed records, malformed-line count, file existed).
    Only takes the cross-process lock (SHARED, never EXCLUSIVE -- this
    never blocks a concurrent writer for longer than one already-brief
    append/rotation critical section, and never blocks another
    concurrent reader) when the file already exists, so running this
    CLI never itself creates a lock file for a journal nothing has
    ever written to."""
    if not path.exists():
        return [], 0, False

    import fcntl  # POSIX-only; deferred so importing this module never breaks Windows.

    lock_path = path.with_name(path.name + ".lock")
    try:
        lock_file = open(lock_path, "a")
    except OSError:
        lock_file = None  # best-effort: still read, just without the lock

    try:
        if lock_file is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            if not path.exists():
                return [], 0, False
            text = path.read_text(encoding="utf-8")
        finally:
            if lock_file is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        if lock_file is not None:
            lock_file.close()

    records: list[dict] = []
    malformed = 0
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        try:
            records.append(json.loads(raw_line))
        except json.JSONDecodeError:
            malformed += 1
    return records, malformed, True


def find_attempt(scan_attempt_id: str, *, request_id: str | None = None) -> dict[str, Any]:
    if not _ATTEMPT_ID_PATTERN.match(scan_attempt_id):
        raise ValueError("scan_attempt_id must be exactly 16 ASCII decimal digits.")

    base_path = Path(settings.SCAN_DIAGNOSTICS_PATH)
    files = _candidate_files(base_path, settings.SCAN_DIAGNOSTICS_BACKUP_COUNT)

    matched: list[dict] = []
    malformed_total = 0
    files_checked: list[dict] = []
    for path in files:
        records, malformed, existed = _read_lines_safely(path)
        malformed_total += malformed
        files_checked.append({"path": str(path), "existed": existed, "malformedLines": malformed})
        for record in records:
            if record.get("scanAttemptId") != scan_attempt_id:
                continue
            if request_id is not None and record.get("requestId") != request_id:
                continue
            matched.append(record)

    # Chronological by the server's OWN receipt timestamp only -- a
    # client-origin event's `occurredAt` is the device clock and is
    # explicitly untrusted for cross-system ordering (contract doc §5).
    matched.sort(key=lambda r: r.get("timestamp") or "")

    # Heuristic, not proof (see the note below): a single real attempt
    # has exactly one backend-side request with requestSequence == 1
    # (the first request of that attempt; a retry of the SAME attempt
    # increments the sequence instead). More than one such request
    # sharing this scanAttemptId suggests either two independent
    # attempts colliding on the same 16-digit id (see the contract
    # doc's residual-collision-risk note), or two owners whose attempt
    # ids happen to match -- this journal deliberately does not store a
    # user id (see app.core.scan_diagnostics's module docstring), so
    # this CLI cannot rule either possibility out; it can only flag it.
    first_requests = {
        record.get("requestId")
        for record in matched
        if record.get("origin") == "backend" and record.get("requestSequence") == 1
    }
    ambiguous_collision = len(first_requests) > 1
    ambiguous_collision_note = (
        "More than one backend request with requestSequence=1 shares this "
        "scanAttemptId. This journal does not store an owner/user id "
        "(deliberate -- see docs/SCAN_ATTEMPT_DIAGNOSTICS.md section 5), so "
        "this may be two different attempts (possibly different owners) "
        "colliding on the same id, or may be benign (e.g. a client that "
        "reused an id after a bug). Do not assume either without further "
        "investigation -- never silently merge these into one attempt."
        if ambiguous_collision
        else None
    )

    return {
        "scanAttemptId": scan_attempt_id,
        "requestIdFilter": request_id,
        "matchCount": len(matched),
        "origins": sorted({r.get("origin") for r in matched if r.get("origin")}),
        "requestIds": sorted({r.get("requestId") for r in matched if r.get("requestId")}),
        "ambiguousCollision": ambiguous_collision,
        "ambiguousCollisionNote": ambiguous_collision_note,
        "filesChecked": files_checked,
        "malformedLineCount": malformed_total,
        "rotationCaveat": (
            "Only the current file and up to "
            f"{settings.SCAN_DIAGNOSTICS_BACKUP_COUNT} rotated backup(s) are "
            "retained -- the journal is size-bounded "
            "(SCAN_DIAGNOSTICS_MAX_BYTES) and older entries may already have "
            "rotated out. A missing event or stage here is never proof that "
            "step did not happen."
        ),
        "events": matched,
    }


def _human_readable(report: dict[str, Any]) -> str:
    lines = [
        f"scanAttemptId: {report['scanAttemptId']}",
        f"matches: {report['matchCount']} across origins {report['origins']}",
    ]
    if report["ambiguousCollisionNote"]:
        lines.append(f"WARNING: {report['ambiguousCollisionNote']}")
    if report["malformedLineCount"]:
        lines.append(f"WARNING: skipped {report['malformedLineCount']} malformed retained line(s).")
    lines.append(report["rotationCaveat"])
    lines.append("")
    for event in report["events"]:
        timestamp = event.get("timestamp", "?")
        origin = event.get("origin", "?")
        stage = event.get("stage", "?")
        outcome = event.get("outcome", "?")
        identity = event.get("requestId") or event.get("eventId") or "?"
        lines.append(f"{timestamp}  [{origin:7}] id={identity}  stage={stage}  outcome={outcome}")
    if not report["events"]:
        lines.append("(no matching records in the currently retained journal file(s))")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read-only lookup of one scan-attempt id in the scan-diagnostics journal."
    )
    parser.add_argument("scan_attempt_id", help="Exactly 16 ASCII decimal digits.")
    parser.add_argument("--request-id", default=None, help="Optionally narrow to one internal requestId.")
    parser.add_argument("--human", action="store_true", help="Human-readable output instead of JSON.")
    args = parser.parse_args()
    try:
        result = find_attempt(args.scan_attempt_id, request_id=args.request_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    print(_human_readable(result) if args.human else json.dumps(result, indent=2))
