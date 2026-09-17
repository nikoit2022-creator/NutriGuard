"""
Application-level exceptions.

Each exception maps 1:1 to an `error.code` value defined in section 4.2
of the API Contract, and carries the HTTP status that should be returned.
These are translated to the standard error envelope by the exception
handlers registered in `app/main.py`.
"""
from typing import Any, Optional


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


class InternalError(AppError):
    code = "INTERNAL_ERROR"
    status_code = 500
