"""
Structured logging setup using structlog.

Never log secrets: passwords, tokens, API keys, or raw health-profile
payloads. Log identifiers (device_id, user_id, request_id) instead of
sensitive content.
"""
import logging
import sys

import structlog

from app.core.config import settings

_SENSITIVE_KEYS = {
    "password",
    "access_token",
    "refresh_token",
    "authorization",
    "gemini_api_key",
    "jwt_secret",
    "token",
}


def _scrub_sensitive(_, __, event_dict):
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO if settings.ENVIRONMENT != "development" else logging.DEBUG,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _scrub_sensitive,
            structlog.processors.JSONRenderer()
            if settings.ENVIRONMENT != "development"
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "nutriguard"):
    return structlog.get_logger(name)
