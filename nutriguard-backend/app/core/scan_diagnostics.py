"""Small opt-in diagnostic journal for temporary product-scan testing.

One compact JSON line is written per image scan. Images, OCR text, model
responses, credentials, user IDs and health-profile data are deliberately
excluded. Rotation is bounded by configuration so diagnostics cannot grow
without limit.
"""

import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from typing import Any

from app.core.config import settings

_lock = Lock()
_handler: RotatingFileHandler | None = None


def _get_handler() -> RotatingFileHandler | None:
    global _handler
    if not settings.SCAN_DIAGNOSTICS_ENABLED:
        return None
    with _lock:
        if _handler is not None:
            return _handler
        path = Path(settings.SCAN_DIAGNOSTICS_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path,
            maxBytes=settings.SCAN_DIAGNOSTICS_MAX_BYTES,
            backupCount=settings.SCAN_DIAGNOSTICS_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _handler = handler
        return handler


def record_scan_diagnostic(**fields: Any) -> None:
    """Best-effort write; diagnostic I/O must never break a scan."""
    try:
        handler = _get_handler()
        if handler is None:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backendVersion": settings.APP_VERSION,
            **{key: value for key, value in fields.items() if value is not None},
        }
        record = logging.LogRecord(
            name="nutriguard.scan_diagnostics",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
            args=(),
            exc_info=None,
        )
        handler.emit(record)
    except Exception:
        # This journal is observational only. Normal application logging
        # remains responsible for reporting operational failures.
        return
