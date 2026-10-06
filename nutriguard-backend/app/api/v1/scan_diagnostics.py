"""
POST /api/v1/scan-diagnostics/client-events (issue #30).

Additive, authenticated ingestion of bounded client-observed scan
diagnostic events. See docs/SCAN_ATTEMPT_DIAGNOSTICS.md for the full
contract. This endpoint never reads or writes the application's own
Postgres database -- events are written to the same bounded,
multi-process-safe journal `app.core.scan_diagnostics` already uses for
backend-side scan diagnostics (see that module), tagged
`origin="android"`. Replay dedup and truthful acknowledgment are
coordinated through a SEPARATE small SQLite ledger
(`app.core.client_event_ledger`) -- also not the application database,
and also bounded.

`settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES` is enforced
in `app.core.body_size_limit.BodySizeLimitMiddleware`, NOT here --
Codex review round 3 found that a route-level dependency runs too late
to bound this route's own body-buffering; see that module's docstring.
"""
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user_id
from app.core import client_event_ledger, scan_diagnostics
from app.core.owner_scope import pseudonymous_owner_scope
from app.core.rate_limit import DIAGNOSTICS_RATE, limiter
from app.schemas.scan_diagnostics import ClientDiagnosticEvent, ClientEventBatchRequest, ClientEventBatchResponse

router = APIRouter(prefix="/scan-diagnostics", tags=["scan-diagnostics"])

_logger = structlog.get_logger(__name__)


def _process_one_event(owner_scope: str, event: ClientDiagnosticEvent) -> str:
    """Returns exactly one of `"accepted"` / `"duplicate"` /
    `"retryable"` for this one event -- NEVER raises (Codex review
    round 2, finding 2: "ordinary scanning must remain unaffected by
    diagnostic failures" applies equally to this diagnostics-only
    endpoint's OWN internal failures; one event's ledger/journal
    malfunction must not crash the whole batch, and must never be
    reported as falsely accepted -- see module docstring and
    app.core.client_event_ledger)."""
    try:
        reservation = client_event_ledger.reserve(owner_scope, event.event_id)
    except Exception:  # noqa: BLE001
        _logger.warning("client_event_ledger_reserve_failed")
        return "retryable"

    if reservation.outcome is client_event_ledger.ReservationOutcome.DUPLICATE:
        return "duplicate"
    if reservation.outcome is client_event_ledger.ReservationOutcome.IN_FLIGHT:
        return "retryable"

    # WON the reservation -- attempt the real, durable write. Only a
    # `True` return (an actual, successful append -- see
    # `record_scan_diagnostic`'s docstring) may ever become "accepted".
    try:
        written = scan_diagnostics.record_scan_diagnostic(
            origin="android",
            ownerScope=owner_scope,
            eventId=event.event_id,
            scanAttemptId=event.scan_attempt_id,
            requestSequence=event.request_sequence,
            sequence=event.sequence,
            occurredAt=event.occurred_at.isoformat(),
            stage=event.stage.value,
            outcome=event.outcome.value,
            durationMs=event.duration_ms,
            appVersion=event.app_version,
            reasonCode=event.reason_code.value if event.reason_code else None,
            metrics=event.metrics,
        )
    except Exception:  # noqa: BLE001
        written = False

    try:
        if written:
            client_event_ledger.commit(owner_scope, event.event_id)
        else:
            client_event_ledger.release(owner_scope, event.event_id)
    except Exception:  # noqa: BLE001
        _logger.warning("client_event_ledger_finalize_failed")
        # The reservation's actual on-disk state is now unknown (commit
        # / release itself failed) -- report retryable rather than risk
        # a false "accepted"; a future resubmission's own `reserve()`
        # call will resolve whatever state actually landed.
        return "retryable"

    return "accepted" if written else "retryable"


@router.post("/client-events", response_model=ClientEventBatchResponse)
@limiter.limit(DIAGNOSTICS_RATE)
async def submit_client_events(
    request: Request,
    batch: ClientEventBatchRequest,
    user_id: UUID = Depends(get_current_user_id),
) -> ClientEventBatchResponse:
    # A stable, one-way pseudonymous scope derived from the authenticated
    # user id (see app.core.owner_scope) -- the raw user id itself is
    # still deliberately never written to the journal or the ledger
    # (see app.core.scan_diagnostics's module docstring), but this scope
    # IS persisted (both as the ledger's dedup key and as the journal
    # line's own `ownerScope` field) so a resubmission is recognized
    # across worker processes/restarts, and so the operator CLI can tell
    # two different owners' attempts apart (Codex review round 2,
    # finding 4).
    owner_scope = pseudonymous_owner_scope(user_id)

    accepted_event_ids: list[str] = []
    duplicate_event_ids: list[str] = []
    retryable_event_ids: list[str] = []
    for event in batch.events:
        category = _process_one_event(owner_scope, event)
        if category == "accepted":
            accepted_event_ids.append(event.event_id)
        elif category == "duplicate":
            duplicate_event_ids.append(event.event_id)
        else:
            retryable_event_ids.append(event.event_id)

    return ClientEventBatchResponse(
        accepted_event_ids=accepted_event_ids,
        duplicate_event_ids=duplicate_event_ids,
        retryable_event_ids=retryable_event_ids,
    )
