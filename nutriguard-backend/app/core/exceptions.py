"""
Application-level exceptions.

Each exception maps 1:1 to an `error.code` value defined in section 4.2
of the API Contract, and carries the HTTP status that should be returned.
These are translated to the standard error envelope by the exception
handlers registered in `app/main.py`.
"""
import time
from typing import Any, Optional


def error_envelope(code: str, message: str, details: Optional[Any] = None) -> dict:
    """The standard `{"error": {...}}` JSON envelope (API Contract
    section 4.2). Shared so anything that must produce this exact shape
    OUTSIDE FastAPI's own exception-handler pipeline (e.g.
    `app.core.body_size_limit`, a pure ASGI middleware that runs ahead
    of routing -- see its module docstring for why) renders
    byte-for-byte the same body `app.main`'s own exception handlers
    already produce for every `AppError`."""
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "timestamp": int(time.time() * 1000),
        }
    }


class AppError(Exception):
    """Base class for all handled application errors."""

    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        details: Optional[Any] = None,
        *,
        diagnostic_metadata: Optional[dict] = None,
    ):
        self.message = message
        self.details = details
        # INTERNAL-ONLY, never part of the public error envelope (see
        # `app.main`'s `handle_app_error`, which reads only
        # `code`/`message`/`details` -- this attribute is never touched
        # there). A side channel for bounded, already-observed
        # request-scoped diagnostic data (e.g. a translation-attempt
        # summary) that a raise site wants preserved in the internal
        # scan-diagnostics journal even though the request itself is
        # failing/partial -- see `app.api.v1.scan`'s exception handlers.
        # Never put anything here that would be unsafe in `details`
        # anyway EXCEPT for the specific reason it must not double as
        # public API surface (still no raw label/OCR text, no
        # credentials -- the diagnostics journal's own privacy limits
        # still apply in full).
        self.diagnostic_metadata = diagnostic_metadata
        super().__init__(message)


class InvalidTokenError(AppError):
    code = "INVALID_TOKEN"
    status_code = 401


class ValidationAppError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class ProductNotFoundError(AppError):
    code = "PRODUCT_NOT_FOUND"
    status_code = 404


class IngredientNotFoundError(AppError):
    code = "INGREDIENT_NOT_FOUND"
    status_code = 404


class ImageTooLargeError(AppError):
    code = "IMAGE_TOO_LARGE"
    status_code = 400


class ImageUnreadableError(AppError):
    code = "IMAGE_UNREADABLE"
    status_code = 422


class AIServiceUnavailableError(AppError):
    """
    Raised ONLY when both the Gemini call AND the deterministic local
    fallback have failed (see API Contract section 7.4). Under normal
    circumstances this should never surface to the client, because the
    fallback chain absorbs Gemini failures silently.
    """

    code = "AI_SERVICE_UNAVAILABLE"
    status_code = 503


class TranslationUnreliableError(AppError):
    """
    Raised when label/OCR text in a language other than English or
    Bulgarian could not be translated reliably enough to persist --
    the AI translation call failed, its response was not valid
    structured JSON, or its own reported confidence was below the
    minimum threshold (see `app.services.label_language`). Signals the
    client to capture a clearer label rather than receiving fabricated
    or low-quality translated data.
    """

    code = "LABEL_TRANSLATION_UNRELIABLE"
    status_code = 422


class RateLimitExceededError(AppError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429


class PayloadTooLargeError(AppError):
    """Issue #30: the raw request body for a client diagnostic-event
    batch exceeded `settings.SCAN_DIAGNOSTICS_CLIENT_EVENTS_MAX_BODY_BYTES`.

    Codex review round 3: no longer raised directly. FastAPI reads and
    buffers a route's ENTIRE request body (to parse its declared
    Pydantic body parameter) before resolving any of that route's own
    `Depends()`, so a dependency can never bound that buffering -- see
    `app.core.body_size_limit`, the pure ASGI middleware that now
    enforces this limit ahead of routing instead. It sends this class's
    `code`/`status_code` directly (it runs outside FastAPI's own
    exception-handler pipeline, so it cannot raise and let
    `app.main.handle_app_error` catch it) -- kept here as the single
    source of truth for this error code/status, consistent with every
    other `AppError` subclass in this module."""

    code = "PAYLOAD_TOO_LARGE"
    status_code = 413


class InternalError(AppError):
    code = "INTERNAL_ERROR"
    status_code = 500
