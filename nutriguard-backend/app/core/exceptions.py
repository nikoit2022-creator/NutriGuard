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

    def __init__(self, message: str, details: Optional[Any] = None):
        self.message = message
        self.details = details
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


class RateLimitExceededError(AppError):
    code = "RATE_LIMIT_EXCEEDED"
    status_code = 429


class InternalError(AppError):
    code = "INTERNAL_ERROR"
    status_code = 500
