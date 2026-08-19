from pydantic import Field

from app.models.enums import Platform
from app.schemas.common import ORMModel


class DeviceAuthRequest(ORMModel):
    device_id: str = Field(min_length=1, max_length=128)
    app_version: str = Field(default="", max_length=32)
    platform: Platform = Platform.ANDROID


class TokenResponse(ORMModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int
    user_id: str


class RefreshRequest(ORMModel):
    refresh_token: str
