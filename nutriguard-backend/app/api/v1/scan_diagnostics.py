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
"""
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user_id
from app.core import client_event_ledger, scan_diagnostics
from app.core.config import settings
from app.core.exceptions import PayloadTooLargeError
from app.core.owner_scope import pseudonymous_owner_scope
from app.core.rate_limit import DIAGNOSTICS_RATE, limiter
from app.schemas.scan_diagnostics import ClientDiagnosticEvent, ClientEventBatchRequest, ClientEventBatchResponse

router = APIRouter(prefix="/scan-diagnostics", tags=["scan-diagnostics"])

_logger = structlog.get_logger(__name__)


async def _enforce_body_size(request: Request) -> None:
    """Runs as a dependency, resolved independently of (and before) the
    declared `body: ClientEventBatchRequest` parameter is parsed and
    validated -- an oversized raw body is rejected here with a bounded
    read, rather than first being fully parsed by Pydantic. Declaring
    `body` as a normal FastAPI parameter (instead of manually parsing
    JSON) is deliberate: it gives Android/Codex a real, complete request
    schema in `openapi.json`, not just the response shape.

    Codex review round 2, finding 6: the previous implementation still
    called `await request.body()` (Starlette's own default -- reads and
    buffers the ENTIRE body into memory before returning it), so a
    request with a missing/lying `Content-Length` (or a chunked-transfer
    body) was fully buffered before this function's own size check ever
    ran -- the `Content-Length` pre-check above was the only real bound,
    and it trivially doesn't apply when that header is absent or
    understated. This now reads the body as a STREAM, in bounded
    chunks, and raises the moment the running total exceeds the limit
    -- never buffering more than `MAX_BODY_BYTES` (plus at most one
    chunk) at once, regardless of what any header claims."""
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit():
        if int(content_length) > settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES:
            raise PayloadTooLargeError(
                "Request body exceeds the "
                f"{settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES}-byte limit."
            )

    limit = settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise PayloadTooLargeError(f"Request body exceeds the {limit}-byte limit.")
        chunks.append(chunk)
    # Starlette's own `Request.body()` -- which FastAPI calls internally
    # to parse the declared `body: ClientEventBatchRequest` parameter --
    # only re-reads the stream when `self._body` is not already set;
    # setting it here (to the SAME bytes it would otherwise have read
    # itself, now already verified within bound) means that later,
    # internal read returns this cached value instead of attempting a
    # second stream iteration (which Starlette would reject outright --
    # a stream can only be consumed once).
    request._body = b"".join(chunks)


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
    _size_checked: None = Depends(_enforce_body_size),
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
