"""
Read-only operator CLI: look up one scan-attempt id across the bounded
scan-diagnostics journal (`app.core.scan_diagnostics`) and its rotated
backup file(s) (issue #30, see docs/SCAN_ATTEMPT_DIAGNOSTICS.md
section 7). Never writes, deletes, or otherwise mutates anything --
a plain reader, safe to run against a live, actively-written journal.

    python -m app.seed.scan_attempt_trace 0042135790246813
    python -m app.seed.scan_attempt_trace 0042135790246813 --human
    python -m app.seed.scan_attempt_trace 0042135790246813 --request-id <uuid>

Placed alongside the other operator/maintenance CLIs in `app/seed/`
(see `ingredient_candidate_maintenance.py`) even though it never
touches the database -- this is diagnostics-file tooling, not seeding.

Locking (Codex review round 2, finding 5): every candidate file (the
current journal AND every retained rotated backup) is protected by the
SAME SINGLE lock the writer itself uses --
`app.core.scan_diagnostics._append_locked`'s sibling `<journal>.lock`
file, taken here in SHARED mode (`fcntl.LOCK_SH`) for the ENTIRE read of
every candidate file, not a separate lock per file. Two things this
fixes over a previous, broken version of this CLI:
  - it used to compute a PER-FILE lock path (`<journal>.N.lock` for a
    rotated backup), which is a lock the writer never takes at all --
    reading a rotated file was therefore never actually synchronized
    with a concurrent rotation, and running this CLI against a rotated
    file left behind a stray, writer-irrelevant `.N.lock` file on disk;
  - it used to acquire and release the lock separately per file, so a
    rotation could happen IN BETWEEN reading the current file and
    reading a backup -- an inconsistent snapshot (e.g. double-counting
    or missing a record that moved from one file to the other mid-read)
    was possible. Holding ONE lock across every file in this single
    invocation gives one consistent, atomic-with-respect-to-rotation
    snapshot instead.

Corrected claim (was previously overstated in this docstring): a SHARED
lock never blocks another concurrent reader, and never blocks a
concurrent writer for longer than this invocation's own read of every
candidate file takes -- bounded by the journal's own configured size
cap (`SCAN_DIAGNOSTICS_MAX_BYTES * (1 + SCAN_DIAGNOSTICS_BACKUP_COUNT)`),
NOT "at most one append", since this CLI may read multiple files under
the one lock. A writer blocked waiting for this lock still only waits
for a bounded amount of time, but it is the time to read the whole
retained journal, not one line.

If the lock file cannot be opened at all (e.g. an unwritable parent
directory), this CLI still reads best-effort WITHOUT the lock -- but
says so explicitly in the report's own `lockAcquired: false` field and
`--human` output, rather than silently claiming a consistent read it
did not actually get.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.owner_scope import UNKNOWN_OWNER_SCOPE

_ATTEMPT_ID_PATTERN = re.compile(r"^[0-9]{16}$")


def _candidate_files(base_path: Path, backup_count: int) -> list[Path]:
    files = [base_path]
    for index in range(1, max(backup_count, 0) + 1):
        files.append(base_path.with_name(base_path.name + f".{index}"))
    return files


def _parse_lines(text: str) -> tuple[list[dict], int]:
    """Returns (parsed object records, malformed-line count). A line
    that is syntactically valid JSON but not a JSON OBJECT (e.g. a bare
    array, string, or number) is counted as malformed too -- every real
    record `.get(...)`s fields, which only a `dict` supports."""
    records: list[dict] = []
    malformed = 0
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if not isinstance(parsed, dict):
            malformed += 1
            continue
        records.append(parsed)
    return records, malformed


def _read_all_files_consistently(files: list[Path]) -> tuple[list[dict], list[dict], int, bool]:
    """Reads every candidate file under ONE shared lock (see module
    docstring), so no rotation can happen in between two files' reads.
    Returns (all parsed records across all files, per-file status
    entries, total malformed-line count, whether the lock was actually
    acquired)."""
    lock_path = files[0].with_name(files[0].name + ".lock")
    lock_file = None
    lock_acquired = False
    try:
        lock_file = open(lock_path, "a")
    except OSError:
        lock_file = None  # best-effort: still read, just without the lock -- reported honestly below.

    try:
        if lock_file is not None:
            import fcntl  # POSIX-only; deferred so importing this module never breaks Windows.

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
                lock_acquired = True
            except OSError:
                lock_acquired = False  # best-effort: still read, just without the lock.
        try:
            all_records: list[dict] = []
            files_checked: list[dict] = []
            malformed_total = 0
            for path in files:
                if not path.exists():
                    files_checked.append({"path": str(path), "existed": False, "malformedLines": 0})
                    continue
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError as exc:
                    # Unreadable (permissions, a race where it was
                    # deleted between `.exists()` and `.read_text()`,
                    # etc.) -- reported per-file, never crashes the
                    # whole lookup.
                    files_checked.append(
                        {"path": str(path), "existed": True, "malformedLines": 0, "unreadable": str(exc)}
                    )
                    continue
                records, malformed = _parse_lines(text)
                malformed_total += malformed
                files_checked.append({"path": str(path), "existed": True, "malformedLines": malformed})
                all_records.extend(records)
            return all_records, files_checked, malformed_total, lock_acquired
        finally:
            if lock_acquired:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        if lock_file is not None:
            lock_file.close()


def _owner_scope_of(record: dict) -> str:
    scope = record.get("ownerScope")
    return scope if isinstance(scope, str) and scope else UNKNOWN_OWNER_SCOPE


def _sort_key(record: dict) -> str:
    # Chronological by the server's OWN receipt timestamp only -- a
    # client-origin event's `occurredAt` is the device clock and is
    # explicitly untrusted for cross-system ordering (contract doc §5).
    # Coerced to `str` defensively: a malformed record could have a
    # non-string `timestamp` (an int, a list, ...), and comparing mixed
    # types would crash `sort` outright rather than just sorting oddly.
    value = record.get("timestamp")
    return value if isinstance(value, str) else ""


def find_attempt(scan_attempt_id: str, *, request_id: str | None = None) -> dict[str, Any]:
    if not _ATTEMPT_ID_PATTERN.match(scan_attempt_id):
        raise ValueError("scan_attempt_id must be exactly 16 ASCII decimal digits.")

    base_path = Path(settings.SCAN_DIAGNOSTICS_PATH)
    files = _candidate_files(base_path, settings.SCAN_DIAGNOSTICS_BACKUP_COUNT)
    all_records, files_checked, malformed_total, lock_acquired = _read_all_files_consistently(files)

    matched: list[dict] = []
    for record in all_records:
        if record.get("scanAttemptId") != scan_attempt_id:
            continue
        if request_id is not None and record.get("requestId") != request_id:
            continue
        matched.append(record)

    matched.sort(key=_sort_key)

    # Heuristic, not proof (see the note below): a single real attempt
    # has exactly one backend-side request with requestSequence == 1
    # (the first request of that attempt; a retry of the SAME attempt
    # increments the sequence instead). More than one such request
    # sharing this scanAttemptId suggests either two independent
    # attempts colliding on the same 16-digit id, or two owners whose
    # attempt ids happen to match.
    first_requests = {
        record.get("requestId")
        for record in matched
        if record.get("origin") == "backend"
        and record.get("requestSequence") == 1
        and isinstance(record.get("requestId"), str)
    }
    sequence_heuristic_collision = len(first_requests) > 1

    # Codex review round 2, finding 4: a DEFINITE signal, not just a
    # heuristic -- every journal line now carries a stable, pseudonymous
    # `ownerScope` (see app.core.owner_scope). More than one DISTINCT,
    # KNOWN owner scope among the matched records for this one
    # `scanAttemptId` is conclusive proof of a collision (or a shared
    # device/token, but never "the same owner"), not merely a
    # suspicion -- `UNKNOWN_OWNER_SCOPE` (a record with no `ownerScope`
    # at all, e.g. an older retained line predating this field) is
    # explicitly excluded from this set rather than silently counted as
    # one more "owner", since it carries no real signal either way.
    known_owner_scopes = {
        _owner_scope_of(record) for record in matched if _owner_scope_of(record) != UNKNOWN_OWNER_SCOPE
    }
    unknown_owner_scope_present = any(_owner_scope_of(record) == UNKNOWN_OWNER_SCOPE for record in matched)
    definite_owner_collision = len(known_owner_scopes) > 1

    ambiguous_collision = sequence_heuristic_collision or definite_owner_collision
    if definite_owner_collision:
        ambiguous_collision_note = (
            f"{len(known_owner_scopes)} DIFFERENT owner scopes share this scanAttemptId -- this is "
            "conclusive: these records do not all belong to the same authenticated owner. Never "
            "treat them as one attempt/session."
        )
    elif sequence_heuristic_collision:
        ambiguous_collision_note = (
            "More than one backend request with requestSequence=1 shares this scanAttemptId. All "
            "matched records share the same ownerScope (or carry none), so this journal cannot rule "
            "out either possibility: two independent attempts by the SAME owner colliding on the same "
            "id, or a client that reused an id after a bug. Do not assume either without further "
            "investigation -- never silently merge these into one attempt."
        )
    else:
        ambiguous_collision_note = None

    return {
        "scanAttemptId": scan_attempt_id,
        "requestIdFilter": request_id,
        "matchCount": len(matched),
        "origins": sorted({r.get("origin") for r in matched if isinstance(r.get("origin"), str)}),
        "requestIds": sorted({r.get("requestId") for r in matched if isinstance(r.get("requestId"), str)}),
        "ownerScopes": sorted(known_owner_scopes),
        "unknownOwnerScopePresent": unknown_owner_scope_present,
        "ambiguousCollision": ambiguous_collision,
        "ambiguousCollisionNote": ambiguous_collision_note,
        "lockAcquired": lock_acquired,
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
    if not report["lockAcquired"]:
        lines.append(
            "WARNING: the journal lock file could not be opened -- this read is best-effort and may "
            "not be consistent with a concurrent writer/rotation."
        )
    if report["ambiguousCollisionNote"]:
        lines.append(f"WARNING: {report['ambiguousCollisionNote']}")
    if report["unknownOwnerScopePresent"]:
        lines.append(
            "NOTE: at least one matched record has no ownerScope (an older retained line) -- it "
            "cannot be attributed to, or ruled out from, any owner shown above."
        )
    if report["malformedLineCount"]:
        lines.append(f"WARNING: skipped {report['malformedLineCount']} malformed retained line(s).")
    for f in report["filesChecked"]:
        if f.get("unreadable"):
            lines.append(f"WARNING: {f['path']} could not be read: {f['unreadable']}")
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
