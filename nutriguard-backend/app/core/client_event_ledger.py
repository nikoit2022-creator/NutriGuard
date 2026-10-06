"""
Cross-process-safe, bounded ledger for client-submitted scan-diagnostic
events (issue #30, Codex review round 2, findings 2-3).

Backs `POST /api/v1/scan-diagnostics/client-events`' truthful
acknowledgment: an `eventId` is only ever reported in `acceptedEventIds`
once it has ACTUALLY been durably written to the scan-diagnostics
journal (`app.core.scan_diagnostics`) -- never before, and never when
that write is skipped (diagnostics disabled) or fails (I/O error).
Backed by a small SQLite database (WAL mode) -- NOT the application's
own Postgres database, never touched by this module -- because
SQLite's own file locking gives real cross-process mutual exclusion for
the reserve-then-commit protocol below, replacing the previous
in-memory-only (single worker process) dedup cache, which could never
coordinate across the multiple Uvicorn worker processes production
actually runs (see `docker-compose.prod.yml`, `--workers 4`).

Protocol (atomic reserve, then commit-or-release), safe against two
concurrent retries of the same event landing on different worker
processes:
  1. `reserve(owner_scope, event_id)` -- a single SQLite transaction
     (`BEGIN IMMEDIATE`, which SQLite blocks/queues across processes,
     not just threads) that atomically checks for an existing row and,
     if none exists, inserts one in the `RESERVED` state. Reports
     whether THIS call won the reservation, the pair was already
     `COMMITTED` (a true, durable duplicate), or another attempt has it
     `RESERVED` right now (a concurrent in-flight retry -- neither a
     duplicate nor safe to also write).
  2. Only on `WON`: the caller (the endpoint) attempts the real journal
     write. On success, `commit(...)` flips the row to `COMMITTED` --
     THIS is the instant the event becomes a recognized duplicate for
     any future resubmission, matching the instant it actually became
     durable, not before. On failure (the write raised, or was skipped
     because diagnostics are disabled), `release(...)` deletes the
     reservation so a future retry can win it again -- an event that
     was never actually stored is never left as a permanent phantom
     duplicate, and never falsely acknowledged either.

Bounded and self-healing, not permanently locked: `reserve` also prunes,
in the SAME transaction --
  - `RESERVED` rows older than
    `SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS` (the
    owning worker crashed between reserving and commit/release -- an
    abandoned reservation is reclaimable, never a permanent lock);
  - `COMMITTED` rows beyond
    `SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS` total rows, oldest
    first.
Once a `COMMITTED` row is pruned, that event id is no longer recognized
as a duplicate if resubmitted -- a documented, bounded-storage
limitation (see docs/SCAN_ATTEMPT_DIAGNOSTICS.md), not a correctness
bug: nothing in this system treats dedup as an authoritative business
guarantee.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock

from app.core.config import settings

_RESERVED = "reserved"
_COMMITTED = "committed"

_connection: sqlite3.Connection | None = None
_connection_lock = Lock()


class ReservationOutcome(str, Enum):
    WON = "won"
    DUPLICATE = "duplicate"
    IN_FLIGHT = "in_flight"


@dataclass(frozen=True)
class ReservationResult:
    outcome: ReservationOutcome


def _connect() -> sqlite3.Connection:
    global _connection
    with _connection_lock:
        if _connection is not None:
            return _connection
        path = Path(settings.CLIENT_EVENT_LEDGER_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS client_events (
                owner_scope TEXT NOT NULL,
                event_id TEXT NOT NULL,
                state TEXT NOT NULL,
                reserved_at REAL NOT NULL,
                committed_at REAL,
                PRIMARY KEY (owner_scope, event_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_client_events_reserved_at ON client_events(reserved_at)"
        )
        _connection = conn
        return conn


def reset_for_testing() -> None:
    """Test-only: closes and forgets the cached connection (and its
    on-disk file) so a fresh database is opened on next use -- each
    test gets an isolated ledger."""
    global _connection
    with _connection_lock:
        if _connection is not None:
            _connection.close()
            _connection = None
    path = Path(settings.CLIENT_EVENT_LEDGER_PATH)
    for candidate in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        candidate.unlink(missing_ok=True)


def _prune_locked(conn: sqlite3.Connection) -> None:
    now = time.time()
    stale_cutoff = now - settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS
    conn.execute("DELETE FROM client_events WHERE state = ? AND reserved_at < ?", (_RESERVED, stale_cutoff))
    total = conn.execute("SELECT COUNT(*) FROM client_events").fetchone()[0]
    max_rows = settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS
    if total > max_rows:
        conn.execute(
            "DELETE FROM client_events WHERE rowid IN "
            "(SELECT rowid FROM client_events ORDER BY reserved_at ASC LIMIT ?)",
            (total - max_rows,),
        )


def reserve(owner_scope: str, event_id: str) -> ReservationResult:
    """Atomically claims `(owner_scope, event_id)` for a fresh
    persistence attempt, or reports it as an existing duplicate/
    in-flight attempt. `BEGIN IMMEDIATE` takes SQLite's reserved lock
    up front, so two processes racing the exact same pair can never
    both observe "no existing row" and both insert -- one blocks (up to
    `busy_timeout`) until the other's transaction finishes."""
    conn = _connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _prune_locked(conn)
        existing = conn.execute(
            "SELECT state FROM client_events WHERE owner_scope = ? AND event_id = ?",
            (owner_scope, event_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO client_events (owner_scope, event_id, state, reserved_at, committed_at) "
                "VALUES (?, ?, ?, ?, NULL)",
                (owner_scope, event_id, _RESERVED, time.time()),
            )
            conn.execute("COMMIT")
            return ReservationResult(ReservationOutcome.WON)
        conn.execute("COMMIT")
        state = existing[0]
        if state == _COMMITTED:
            return ReservationResult(ReservationOutcome.DUPLICATE)
        return ReservationResult(ReservationOutcome.IN_FLIGHT)
    except Exception:
        conn.execute("ROLLBACK")
        raise


def commit(owner_scope: str, event_id: str) -> None:
    """Marks a `WON` reservation as durably persisted. Only call this
    AFTER the journal write actually succeeded."""
    conn = _connect()
    conn.execute(
        "UPDATE client_events SET state = ?, committed_at = ? "
        "WHERE owner_scope = ? AND event_id = ? AND state = ?",
        (_COMMITTED, time.time(), owner_scope, event_id, _RESERVED),
    )


def release(owner_scope: str, event_id: str) -> None:
    """Abandons a `WON` reservation whose journal write failed (or was
    skipped because diagnostics are disabled) -- deletes it so a future
    retry can win it again, rather than permanently blocking the event
    id without it ever having actually been stored."""
    conn = _connect()
    conn.execute(
        "DELETE FROM client_events WHERE owner_scope = ? AND event_id = ? AND state = ?",
        (owner_scope, event_id, _RESERVED),
    )
