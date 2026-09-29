import multiprocessing as mp

import pytest

from app.core import client_event_ledger
from app.core.client_event_ledger import ReservationOutcome


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(
        client_event_ledger.settings, "CLIENT_EVENT_LEDGER_PATH", str(tmp_path / "ledger.sqlite3")
    )
    monkeypatch.setattr(
        client_event_ledger.settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS", 30
    )
    monkeypatch.setattr(client_event_ledger.settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS", 20000)
    client_event_ledger.reset_for_testing()
    yield
    client_event_ledger.reset_for_testing()


def test_first_reservation_wins():
    result = client_event_ledger.reserve("owner-1", "event-1")
    assert result.outcome is ReservationOutcome.WON


def test_reservation_still_pending_is_in_flight_not_duplicate():
    client_event_ledger.reserve("owner-1", "event-1")
    # No commit yet -- a concurrent retry of the SAME event must not be
    # treated as a duplicate (nothing durable exists yet) nor allowed to
    # also win (would double-write).
    result = client_event_ledger.reserve("owner-1", "event-1")
    assert result.outcome is ReservationOutcome.IN_FLIGHT


def test_committed_reservation_is_a_true_duplicate():
    client_event_ledger.reserve("owner-1", "event-1")
    client_event_ledger.commit("owner-1", "event-1")
    result = client_event_ledger.reserve("owner-1", "event-1")
    assert result.outcome is ReservationOutcome.DUPLICATE


def test_released_reservation_can_be_won_again():
    client_event_ledger.reserve("owner-1", "event-1")
    client_event_ledger.release("owner-1", "event-1")
    # The write never actually happened (release = abandoned) -- a retry
    # of the SAME event must be able to win again, not be permanently
    # stuck as "in flight" or falsely treated as a duplicate.
    result = client_event_ledger.reserve("owner-1", "event-1")
    assert result.outcome is ReservationOutcome.WON


def test_dedup_is_scoped_per_owner_not_global():
    client_event_ledger.reserve("owner-1", "event-1")
    client_event_ledger.commit("owner-1", "event-1")
    # A different owner submitting the SAME eventId is never treated as
    # a duplicate of another owner's event.
    result = client_event_ledger.reserve("owner-2", "event-1")
    assert result.outcome is ReservationOutcome.WON


def test_stale_reservation_is_reclaimed_after_timeout(monkeypatch):
    monkeypatch.setattr(
        client_event_ledger.settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_RESERVATION_TIMEOUT_SECONDS", 0
    )
    client_event_ledger.reserve("owner-1", "event-1")  # simulates a crash: never committed/released
    # Immediately reclaimable once the timeout is effectively zero --
    # models a worker that died between reserve() and commit()/release().
    result = client_event_ledger.reserve("owner-1", "event-1")
    assert result.outcome is ReservationOutcome.WON


def test_ledger_is_bounded_and_prunes_oldest_first(monkeypatch):
    monkeypatch.setattr(client_event_ledger.settings, "SCAN_DIAGNOSTICS_CLIENT_EVENTS_DEDUP_MAX_ROWS", 3)
    for i in range(5):
        result = client_event_ledger.reserve("owner-1", f"event-{i}")
        client_event_ledger.commit("owner-1", f"event-{i}")
        assert result.outcome is ReservationOutcome.WON

    # The oldest committed rows were pruned once the bound was exceeded
    # -- resubmitting one now looks like a brand new event (documented,
    # bounded-storage limitation, not a bug).
    result = client_event_ledger.reserve("owner-1", "event-0")
    assert result.outcome is ReservationOutcome.WON
    # The most recent one is still tracked as a genuine duplicate.
    result = client_event_ledger.reserve("owner-1", "event-4")
    assert result.outcome is ReservationOutcome.DUPLICATE


# --- Cross-process safety ---------------------------------------------------
#
# Two genuinely separate OS processes (not threads) race the exact same
# (owner_scope, eventId) pair -- this is precisely the scenario the
# previous process-local in-memory cache could never coordinate across
# (production runs multiple Uvicorn worker *processes* sharing nothing
# but the filesystem -- see docker-compose.prod.yml, `--workers 4`).


def _mp_worker_reserve(path_str: str, owner_scope: str, event_id: str, barrier, result_path: str) -> None:
    from app.core.config import settings

    settings.CLIENT_EVENT_LEDGER_PATH = path_str
    barrier.wait()
    result = client_event_ledger.reserve(owner_scope, event_id)
    with open(result_path, "w") as f:
        f.write(result.outcome.value)


def test_concurrent_reserve_across_processes_never_double_wins(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    ctx = mp.get_context("fork")
    n_workers = 8
    barrier = ctx.Barrier(n_workers)
    result_paths = [tmp_path / f"result_{w}.txt" for w in range(n_workers)]
    procs = [
        ctx.Process(
            target=_mp_worker_reserve,
            args=(str(path), "owner-1", "event-1", barrier, str(result_paths[w])),
        )
        for w in range(n_workers)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
    for p in procs:
        assert p.exitcode == 0

    outcomes = [rp.read_text().strip() for rp in result_paths]
    assert outcomes.count(ReservationOutcome.WON.value) == 1
    assert outcomes.count(ReservationOutcome.IN_FLIGHT.value) == n_workers - 1
