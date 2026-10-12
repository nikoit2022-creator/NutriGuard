import hashlib
import json

import pytest

from app.core.config import settings


@pytest.mark.asyncio
async def test_android_release_feed_is_empty_until_published(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ANDROID_RELEASE_DIR", str(tmp_path))
    response = await app_client.get("/api/v1/app-updates/android/latest")
    assert response.status_code == 204
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_android_release_feed_and_apk(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ANDROID_RELEASE_DIR", str(tmp_path))
    apk = b"signed-apk-fixture"
    (tmp_path / "4.apk").write_bytes(apk)
    (tmp_path / "latest.json").write_text(json.dumps({
        "versionCode": 4,
        "versionName": "1.3",
        "sha256": hashlib.sha256(apk).hexdigest(),
        "sizeBytes": len(apk),
        "notes": "New features",
    }))
    response = await app_client.get("/api/v1/app-updates/android/latest")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["versionCode"] == 4
    assert response.json()["sha256"] == hashlib.sha256(apk).hexdigest()
    download = await app_client.get("/api/v1/app-updates/android/4.apk")
    assert download.status_code == 200
    assert download.content == apk
    assert download.headers["content-type"] == "application/vnd.android.package-archive"


@pytest.mark.asyncio
async def test_android_release_feed_does_not_advertise_missing_apk(app_client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "ANDROID_RELEASE_DIR", str(tmp_path))
    (tmp_path / "latest.json").write_text(json.dumps({
        "versionCode": 5,
        "versionName": "1.4",
        "sha256": "a" * 64,
        "sizeBytes": 100,
    }))
    assert (await app_client.get("/api/v1/app-updates/android/latest")).status_code == 503
    assert (await app_client.get("/api/v1/app-updates/android/5.apk")).status_code == 404
