"""
Shared pytest fixtures.

Tests run against an isolated in-memory/temp-file SQLite database via
aiosqlite so the full suite is fast and has zero external
dependencies (no Docker/Postgres required to run `pytest`). The
Postgres-specific DDL (migrations) is exercised separately -- see
`README.md` "How to run tests" for the Postgres integration option.
"""
import asyncio
import os
import uuid

# Ensure test-safe configuration BEFORE any app module is imported, since
# app.core.config.settings is instantiated at import time.
os.environ.setdefault("REDIS_ENABLED", "false")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("GEMINI_API_KEY", "")  # force fallback-only behavior in tests
os.environ.setdefault("ENVIRONMENT", "test")
# Barcode discovery makes real outbound HTTP calls by default (see
# app/services/barcode_discovery.py) -- off by default for the whole
# suite, same rationale as GEMINI_API_KEY above: no test should depend
# on network access. Tests that specifically exercise discovery opt
# back in per-test via `monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)`
# (settings is a mutable singleton -- see app/core/config.py) and always
# replace the provider classes with mocks/fakes too (see
# tests/integration/test_barcode_discovery_flow.py).
os.environ.setdefault("BARCODE_DISCOVERY_ENABLED", "false")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 registers all models
from app.core.security import create_access_token
from app.database.base import Base
from app.database.session import get_db


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def app_client(db_engine):
    from app.main import app

    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    def _make(user_id: uuid.UUID | None = None, device_id: str = "test-device") -> dict:
        uid = str(user_id or uuid.uuid4())
        token = create_access_token(uid, device_id)
        return {"Authorization": f"Bearer {token}", "user_id": uid}

    return _make


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Rate-limit storage is process-global; reset it before every test so
    tests don't spuriously trip each other's limits."""
    from app.core.rate_limit import limiter

    limiter.reset()
    yield
