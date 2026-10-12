"""Public, read-only release feed for privately distributed Android APKs."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from app.core.config import settings

router = APIRouter(prefix="/app-updates/android", tags=["app-updates"])


class AndroidRelease(BaseModel):
    versionCode: int = Field(gt=0)
    versionName: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sizeBytes: int = Field(gt=0)
    notes: str = Field(default="", max_length=500)


def release_dir() -> Path:
    return Path(settings.ANDROID_RELEASE_DIR)


@router.get("/latest", response_model=AndroidRelease, responses={204: {"description": "No release published"}})
def latest_android_release(response: Response):
    response.headers["Cache-Control"] = "no-store"
    manifest = release_dir() / "latest.json"
    if not manifest.is_file():
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    try:
        release = AndroidRelease.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        raise HTTPException(status_code=503, detail="Android release metadata unavailable")
    apk = release_dir() / f"{release.versionCode}.apk"
    try:
        if not apk.is_file() or apk.stat().st_size != release.sizeBytes:
            raise HTTPException(status_code=503, detail="Android release file unavailable")
    except OSError:
        raise HTTPException(status_code=503, detail="Android release file unavailable")
    return release


@router.get(
    "/{version_code}.apk",
    response_class=FileResponse,
    responses={200: {"content": {"application/vnd.android.package-archive": {}}}},
)
def android_release_apk(version_code: int):
    if version_code <= 0:
        raise HTTPException(status_code=404)
    apk = release_dir() / f"{version_code}.apk"
    if not apk.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        apk,
        media_type="application/vnd.android.package-archive",
        filename=f"nutriguard-{version_code}.apk",
        headers={"Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"},
    )
