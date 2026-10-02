#!/usr/bin/env python3
"""Generate the exact EN/BG `GET /api/v1/ingredients/{id}` response
fixtures for Codex's Android work (docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md:
"publish an exact mapping and representative response fixture for each
language and all four identities").

These are REAL bytes: an in-memory SQLite DB is seeded via the actual
`app.seed.load_seed.load_seed()` + `app.seed.load_openfoodtox_pilot_content.run(apply=True)`,
then the real FastAPI app serves each of the four identities through the
real endpoint over a real ASGI transport -- never hand-authored JSON, so
Codex can trust these are exactly what the backend returns, not an
approximation of it.

Usage::

    python -m scripts.openfoodtox.generate_app_pilot_fixtures
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os  # noqa: E402

os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("JWT_SECRET", "fixture-generation-not-for-production")
os.environ.setdefault("GEMINI_API_KEY", "")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("BARCODE_DISCOVERY_ENABLED", "false")

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

import app.models  # noqa: E402,F401
from app.database.base import Base  # noqa: E402
from app.database.session import get_db  # noqa: E402
from app.seed import load_openfoodtox_pilot_content as import_module  # noqa: E402
from app.seed import load_seed as load_seed_module  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_DIR = _BACKEND_ROOT / "docs" / "openfoodtox_app_pilot_fixtures"

_IDENTITY_IDS = {
    "E250": "e250_sodium_nitrite",
    "E150d": "e150d_sulphite_ammonia_caramel",
    "E330": "e330_citric_acid",
    "E951": "e951_aspartame",
}


async def _build_app_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    import_module.AsyncSessionLocal = session_factory
    load_seed_module.AsyncSessionLocal = session_factory

    await load_seed_module.load_seed()
    exit_code = await import_module.run(apply=True)
    if exit_code != 0:
        raise RuntimeError("pilot import failed during fixture generation")

    from app.main import app

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    return client, engine


async def main() -> int:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    client, engine = await _build_app_client()
    try:
        resp = await client.post("/api/v1/auth/device", json={"deviceId": "openfoodtox-fixture-generator"})
        resp.raise_for_status()
        headers = {"Authorization": f"Bearer {resp.json()['accessToken']}"}

        for e_number, ingredient_id in _IDENTITY_IDS.items():
            resp = await client.get(f"/api/v1/ingredients/{ingredient_id}", headers=headers)
            resp.raise_for_status()
            body = resp.json()
            out_path = _OUTPUT_DIR / f"{e_number.lower()}_ingredient_out.json"
            out_path.write_text(json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            print(f"WROTE: {out_path}")
    finally:
        await client.aclose()
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
