"""
Rate limiting per API Contract section 8. Keyed by the authenticated
user id when available, otherwise by client IP (e.g. for /auth/device,
which happens before a token exists).
"""
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings


def _rate_limit_key(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        # Group by token string; a full JWT decode here would be overkill
        # for a rate-limit key and this is still effectively per-device.
        return auth_header[-32:]
    return get_remote_address(request)


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=settings.REDIS_URL if settings.REDIS_ENABLED else "memory://",
)

SCAN_RATE = f"{settings.RATE_LIMIT_SCAN_PER_HOUR}/hour"
READ_RATE = f"{settings.RATE_LIMIT_READ_PER_HOUR}/hour"
PROFILE_RATE = f"{settings.RATE_LIMIT_PROFILE_PER_HOUR}/hour"
AUTH_RATE = f"{settings.RATE_LIMIT_AUTH_PER_MINUTE}/minute"
