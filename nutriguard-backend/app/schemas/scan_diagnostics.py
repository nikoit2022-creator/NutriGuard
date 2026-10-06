"""
Wire schemas for POST /api/v1/scan-diagnostics/client-events (issue #30).

See docs/SCAN_ATTEMPT_DIAGNOSTICS.md for the full contract: field
meanings, the stage/outcome/reason enums, and the privacy allowlist
these schemas enforce (no free-text fields at all -- only bounded
enums, ids and numbers, so no image, OCR/ingredient text, barcode
value, filename/URI, credential, device identifier, or raw
request/response/exception content can ever be submitted here).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import Field, field_validator

from app.core.config import settings
from app.schemas.common import ORMModel

_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_SCAN_ATTEMPT_ID_PATTERN = r"^[0-9]{16}$"
_APP_VERSION_PATTERN = r"^[A-Za-z0-9_.+-]{1,32}$"


class ClientDiagnosticStage(str, Enum):
    """Client-observable lifecycle points for one scan attempt. A
    STRICT subset of the full stage vocabulary documented in
    docs/SCAN_ATTEMPT_DIAGNOSTICS.md -- Android may only ever report
    what it itself observed, never a backend-side stage it cannot see.

    `IMAGE_PREPARATION`/`PARSING`/`PERSISTENCE` added (Codex review
    round 2, finding 7 -- contract reconciliation): the original set
    could not represent client-side image compression/resize before
    upload, client-side response parsing after upload, or the client's
    own local outbox persistence -- each is now its own explicit stage
    rather than being silently folded into `CAPTURE_COMPLETE` or
    `RESPONSE_RECEIVED`."""

    ATTEMPT_START = "ATTEMPT_START"
    CAPTURE_COMPLETE = "CAPTURE_COMPLETE"
    IMAGE_PREPARATION = "IMAGE_PREPARATION"
    UPLOAD_START = "UPLOAD_START"
    UPLOAD_RETRY = "UPLOAD_RETRY"
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    PARSING = "PARSING"
    PERSISTENCE = "PERSISTENCE"
    TERMINAL_SUCCESS = "TERMINAL_SUCCESS"
    TERMINAL_FAILURE = "TERMINAL_FAILURE"
    TERMINAL_CANCELLED = "TERMINAL_CANCELLED"


class ClientDiagnosticOutcome(str, Enum):
    """Per-event lifecycle outcome (distinct from the existing,
    per-request `outcome` string already written by
    `app.api.v1.scan` -- see the contract doc's "outcome means two
    different things by origin" note).

    `PARTIAL`/`INTERRUPTED` added (Codex review round 2, finding 7):
    the original set could not faithfully represent a `labelScanRequired`-
    style partial result (previously had to be misreported as `FAILED`,
    or a fabricated `SUCCEEDED`) or an attempt genuinely interrupted
    (app killed/backgrounded, process death) rather than user-cancelled
    (`CANCELLED`) or network/server-failed (`FAILED`). Never map a
    partial result to `FAILED`, and never invent a `SUCCEEDED` merely to
    fit the old enum -- report what was actually observed."""

    STARTED = "STARTED"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    RETRIED = "RETRIED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ClientDiagnosticReasonCode(str, Enum):
    """Closed, allowlisted reason vocabulary -- never arbitrary text."""

    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    NETWORK_OFFLINE = "NETWORK_OFFLINE"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    AUTH_RETRY = "AUTH_RETRY"
    SERVER_ERROR = "SERVER_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    VALIDATION_REJECTED = "VALIDATION_REJECTED"
    USER_CANCELLED = "USER_CANCELLED"
    DECODE_ERROR = "DECODE_ERROR"
    UNKNOWN = "UNKNOWN"


# Allowlisted metric keys and their inclusive numeric bounds. Any key
# outside this map is rejected -- `metrics` is bounded, numeric-only
# telemetry, never a free-form bag (contract: "No arbitrary text or
# metadata bags").
_METRIC_BOUNDS: dict[str, tuple[float, float]] = {
    "imageWidthPx": (0, 20000),
    "imageHeightPx": (0, 20000),
    "imageBytes": (0, 100 * 1024 * 1024),
    "uploadBytes": (0, 100 * 1024 * 1024),
    "retryCount": (0, 1000),
    "ocrConfidencePct": (0, 100),
    "outboxDepth": (0, 10000),
    "queuedMs": (0, 24 * 60 * 60 * 1000),
}
_MAX_METRIC_ENTRIES = 8


class ClientDiagnosticEvent(ORMModel):
    event_id: str = Field(pattern=_UUID_PATTERN, description="UUID string; used for replay dedup.")
    scan_attempt_id: str = Field(pattern=_SCAN_ATTEMPT_ID_PATTERN)
    # Nullable: an event captured before any network attempt (e.g.
    # ATTEMPT_START/CAPTURE_COMPLETE) has no request sequence yet.
    request_sequence: int | None = Field(default=None, ge=1, le=999_999_999)
    # This event's position in the client's own local outbox for this
    # attempt -- not the same axis as `request_sequence`.
    sequence: int = Field(ge=0, le=100_000)
    # Client (device) clock -- untrusted for cross-system ordering, see
    # the contract doc; used only for display/relative ordering.
    occurred_at: datetime
    stage: ClientDiagnosticStage
    outcome: ClientDiagnosticOutcome
    duration_ms: int | None = Field(default=None, ge=0, le=24 * 60 * 60 * 1000)
    app_version: str = Field(pattern=_APP_VERSION_PATTERN)
    reason_code: ClientDiagnosticReasonCode | None = Field(default=None)
    metrics: dict[str, float] | None = Field(default=None)

    @field_validator("metrics")
    @classmethod
    def _validate_metrics(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        if value is None:
            return None
        if len(value) > _MAX_METRIC_ENTRIES:
            raise ValueError(f"metrics may not have more than {_MAX_METRIC_ENTRIES} entries.")
        for key, metric_value in value.items():
            bounds = _METRIC_BOUNDS.get(key)
            if bounds is None:
                raise ValueError(f"metrics key '{key}' is not in the allowlist.")
            low, high = bounds
            if not isinstance(metric_value, (int, float)) or isinstance(metric_value, bool):
                raise ValueError(f"metrics['{key}'] must be numeric.")
            if not (low <= metric_value <= high):
                raise ValueError(f"metrics['{key}'] must be between {low} and {high}.")
        return value


class ClientEventBatchRequest(ORMModel):
    events: list[ClientDiagnosticEvent] = Field(
        min_length=1, max_length=settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BATCH
    )


class ClientEventBatchResponse(ORMModel):
    # Every `eventId` from the request appears in EXACTLY ONE of these
    # three lists (Codex review round 2, finding 2 -- truthful
    # acknowledgment):
    #   - accepted: durably written to the journal by the time this
    #     response was sent. Safe for the client to drop from its
    #     outbox.
    #   - duplicate: already durably written by an EARLIER accepted
    #     submission. Also safe to drop.
    #   - retryable: NOT durably written (diagnostics disabled, a
    #     journal I/O failure, or a concurrent in-flight retry of the
    #     same event elsewhere) -- the client MUST keep it in its
    #     outbox and retry later; it was never falsely acknowledged.
    accepted_event_ids: list[str]
    duplicate_event_ids: list[str]
    retryable_event_ids: list[str]
