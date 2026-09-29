"""
POST /api/v1/scan-diagnostics/client-events (issue #30).

Additive, authenticated ingestion of bounded client-observed scan
diagnostic events. See docs/SCAN_ATTEMPT_DIAGNOSTICS.md for the full
contract. This endpoint never reads or writes the database -- events
are written to the same bounded, multi-process-safe journal
`app.core.scan_diagnostics` already uses for backend-side scan
diagnostics (see that module), tagged `origin="android"`.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user_id
from app.core import scan_diagnostics
from app.core.config import settings
from app.core.exceptions import PayloadTooLargeError
from app.core.rate_limit import DIAGNOSTICS_RATE, limiter
from app.schemas.scan_diagnostics import ClientEventBatchRequest, ClientEventBatchResponse

router = APIRouter(prefix="/scan-diagnostics", tags=["scan-diagnostics"])


async def _enforce_body_size(request: Request) -> None:
    """Runs as a dependency, resolved independently of (and before) the
    declared `body: ClientEventBatchRequest` parameter is parsed and
    validated -- an oversized raw body is rejected here with a bounded
    read, rather than first being fully parsed by Pydantic. Declaring
    `body` as a normal FastAPI parameter (instead of manually parsing
    JSON) is deliberate: it gives Android/Codex a real, complete request
    schema in `openapi.json`, not just the response shape."""
    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit():
        if int(content_length) > settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES:
            raise PayloadTooLargeError(
                "Request body exceeds the "
                f"{settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES}-byte limit."
            )
    # `Request.body()` caches the bytes it reads (see Starlette), so this
    # does not cause FastAPI's own later body parsing to re-read the
    # stream -- it reads the SAME cached bytes. Covers a missing/lying
    # Content-Length header (e.g. chunked transfer encoding).
    body = await request.body()
    if len(body) > settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES:
        raise PayloadTooLargeError(
            f"Request body exceeds the {settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES}-byte limit."
        )


@router.post("/client-events", response_model=ClientEventBatchResponse)
@limiter.limit(DIAGNOSTICS_RATE)
async def submit_client_events(
    request: Request,
    batch: ClientEventBatchRequest,
    user_id: UUID = Depends(get_current_user_id),
    _size_checked: None = Depends(_enforce_body_size),
) -> ClientEventBatchResponse:
    # Diagnostics ids/attempt ids are pseudonymous strings, never raw
    # PII -- but the authenticated user id itself is deliberately kept
    # OUT of the journal (see app.core.scan_diagnostics's module
    # docstring: user ids are excluded on purpose). It is used here only
    # as the in-memory dedup scope key, never written to disk.
    user_key = str(user_id)

    accepted_event_ids: list[str] = []
    duplicate_event_ids: list[str] = []
    for event in batch.events:
        if scan_diagnostics.is_duplicate_client_event(user_key, event.event_id):
            duplicate_event_ids.append(event.event_id)
            continue
        accepted_event_ids.append(event.event_id)
        scan_diagnostics.record_scan_diagnostic(
            origin="android",
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

    return ClientEventBatchResponse(
        accepted_event_ids=accepted_event_ids,
        duplicate_event_ids=duplicate_event_ids,
    )
