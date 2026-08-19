from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidTokenError
from app.core.security import decode_token
from app.database.session import get_db

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> UUID:
    if credentials is None or not credentials.credentials:
        raise InvalidTokenError("Missing Authorization header.")

    payload = decode_token(credentials.credentials, expected_type="access")
    try:
        return UUID(payload.sub)
    except ValueError as exc:
        raise InvalidTokenError("Token subject is not a valid user id.") from exc


DbSession = AsyncSession
