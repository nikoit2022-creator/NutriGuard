"""
Real PostgreSQL coverage for the OpenFoodTox app pilot import
(`app/seed/load_openfoodtox_pilot_content.py`) -- closes
docs/OPENFOODTOX_APP_INTEGRATION_FIX_TASK.md's "close existing delivery
requirements" item: the prior assignment's Postgres verification only
ran the `a8b9c0d1e2f3` migration cycle on an EMPTY Postgres container
and otherwise relied on the SQLite-backed response-path tests
(`tests/integration/test_openfoodtox_app_pilot_response_paths.py`).
That leaves two real gaps SQLite cannot close:

1. The migration was never proven against a table that already HAD
   rows before it ran (does `owner_approved_without_review` really
   default to `false` for pre-existing data, not just for rows created
   after the column existed?).
2. The import + response serving were never proven through a real
   `asyncpg` connection at all, nor through the barcode-discovery and
   label-image scan paths specifically (only direct/listing/OCR-text/
   partial paths were ever Postgres-adjacent, and even those only via
   SQLite).

Two separate opt-in env vars, deliberately NOT the same one:

- `NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL` -- `TestMigrationWithPreexistingRows`
  below. This class runs REAL `alembic` downgrade/upgrade cycles and
  inserts raw pre-migration rows -- it mutates schema state. Point it
  ONLY at a throwaway, single-purpose, disposable Postgres instance
  never shared with another test run, e.g.:

      docker run --rm -d --name openfoodtox-migration-check \\
          -e POSTGRES_PASSWORD=test -e POSTGRES_DB=nutriguard_test \\
          -p 55434:5432 postgres:16-alpine
      # from an empty DB -- do NOT point this at an instance already at head.
      NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL="postgresql+asyncpg://postgres:test@localhost:55434/nutriguard_test" \\
          pytest tests/postgres/test_openfoodtox_pilot_import_postgres.py::TestMigrationWithPreexistingRows -v
      docker stop openfoodtox-migration-check

- `NUTRIGUARD_TEST_POSTGRES_URL` -- every other class below. Same
  convention as the rest of this directory: point it at an instance
  already migrated to head (`alembic upgrade head`). These tests write
  real catalog rows for the four pilot identities (fixed, well-known
  ids, e.g. `e150d_sulphite_ammonia_caramel`) and real `Product` rows
  with their own fixed barcodes -- run them against a disposable
  instance too, not a long-lived one shared with unrelated data:

      docker run --rm -d --name openfoodtox-pilot-pg \\
          -e POSTGRES_PASSWORD=test -e POSTGRES_DB=nutriguard_test \\
          -p 55433:5432 postgres:16-alpine
      DATABASE_URL="postgresql+asyncpg://postgres:test@localhost:55433/nutriguard_test" alembic upgrade head
      NUTRIGUARD_TEST_POSTGRES_URL="postgresql+asyncpg://postgres:test@localhost:55433/nutriguard_test" \\
          pytest tests/postgres/test_openfoodtox_pilot_import_postgres.py -v
      docker stop openfoodtox-pilot-pg
"""
import io
import json
import os

import pytest
from PIL import Image


# --- 1. Migration-with-pre-existing-rows (destructive; separate opt-in) ----


_MIGRATION_URL_VAR = "NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL"


class TestMigrationWithPreexistingRows:
    pytestmark = pytest.mark.skipif(
        not os.environ.get(_MIGRATION_URL_VAR),
        reason=f"Opt-in: set {_MIGRATION_URL_VAR} to an EMPTY, disposable, single-purpose "
        "Postgres instance (this test runs real alembic downgrade/upgrade cycles).",
    )

    @pytest.mark.asyncio
    async def test_owner_approved_without_review_defaults_false_for_preexisting_rows(self):
        import subprocess
        import sys
        from pathlib import Path

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        url = os.environ[_MIGRATION_URL_VAR]
        backend_root = Path(__file__).resolve().parents[2]
        env = {**os.environ, "DATABASE_URL": url}

        def _alembic(*args: str) -> None:
            subprocess.run(
                [sys.executable, "-m", "alembic", *args],
                cwd=str(backend_root),
                env=env,
                check=True,
                capture_output=True,
            )

        # Pre-migration schema: the revision immediately before the one
        # under test.
        _alembic("upgrade", "d7e8f9a0b1c2")

        engine = create_async_engine(url, future=True)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        """
                        INSERT INTO ingredients (
                          id, common_name, normalized_name, scientific_name, e_number, ins_number,
                          cas_number, category, description, purpose_in_food, health_concerns,
                          evidence_level, countries_restricted_or_banned, efsa_status, fda_status,
                          who_iarc_classification, acceptable_daily_intake, side_effects, allergens,
                          "references", risk_level, risk_assessment_available, verification_status,
                          source, source_record_id, source_url, retrieved_at, last_verified_at,
                          confidence, schema_version, is_gluten, is_lactose, is_vegan, is_vegetarian,
                          is_halal, is_kosher, bad_for_diabetes, bad_for_hypertension,
                          bad_for_kidney_disease, bad_for_gout, bad_for_pregnancy, bad_for_children,
                          bad_for_high_cholesterol, effect_conditions, dietary_guidance
                        ) VALUES (
                          'pg_migration_test_ingredient', 'PG Migration Test Ingredient',
                          'pg migration test ingredient', '', 'E998', '998', NULL,
                          'Preservative', 'Inserted before the owner-approval migration ran.', '', '',
                          '', '', '', '', NULL, '', '', '', '', 'SAFE', true, 'VERIFIED',
                          'CURATED_SEED', 'pg_migration_test_ingredient', NULL, now(), now(), 1.0, 1,
                          NULL, NULL, NULL, NULL, NULL, NULL, false, false, false, false, false,
                          false, false, '', ''
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO ingredient_localizations (
                          ingredient_id, language, common_name, category, description, purpose_in_food,
                          health_concerns, evidence_level, countries_restricted_or_banned, efsa_status,
                          fda_status, acceptable_daily_intake, side_effects, allergens,
                          translation_status, translation_source, source_content_hash, reviewed_at,
                          schema_version, effect_conditions, dietary_guidance
                        ) VALUES (
                          'pg_migration_test_ingredient', 'bg', 'PG Тестова съставка', '', '', '',
                          '', '', '', '', '', '', '', '', 'REVIEWED', 'MACHINE_TRANSLATED',
                          'deadbeef', now(), 1, '', ''
                        )
                        """
                    )
                )
        finally:
            await engine.dispose()

        # The migration under test.
        _alembic("upgrade", "a8b9c0d1e2f3")

        engine = create_async_engine(url, future=True)
        try:
            async with engine.begin() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT owner_approved_without_review, translation_status "
                            "FROM ingredient_localizations WHERE ingredient_id = :id AND language = 'bg'"
                        ),
                        {"id": "pg_migration_test_ingredient"},
                    )
                ).one()
        finally:
            await engine.dispose()

        assert row.owner_approved_without_review is False
        assert row.translation_status == "REVIEWED"

        # Leave the instance ready for a fresh run next time this is used.
        _alembic("downgrade", "d7e8f9a0b1c2")


# --- 2/3. Real import + real barcode/label-image response paths ------------

# Deliberately NOT a module-level `pytestmark` -- that would apply to
# `TestMigrationWithPreexistingRows` above too (pytest's `pytestmark`
# convention is module-wide, regardless of class scoping), incorrectly
# gating its own, separate `NUTRIGUARD_TEST_POSTGRES_MIGRATION_URL`
# opt-in behind THIS var as well. Applied explicitly to each test below
# instead.
_requires_postgres_url = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres instance "
    "already migrated to head.",
)


@pytest.fixture(scope="module")
def postgres_url() -> str:
    return os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]


@pytest.fixture
async def session_factory(postgres_url, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.seed import load_openfoodtox_pilot_content as import_module
    from app.seed import load_seed as load_seed_module

    engine = create_async_engine(postgres_url, future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr(import_module, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
async def pilot_seeded_postgres(session_factory):
    """The real import against a real asyncpg connection -- not SQLite."""
    from app.seed import load_openfoodtox_pilot_content as import_module
    from app.seed import load_seed as load_seed_module

    await load_seed_module.load_seed()
    assert await import_module.run(apply=True) == 0
    return session_factory


@pytest.fixture
async def pg_app_client(postgres_url, pilot_seeded_postgres):
    from httpx import ASGITransport, AsyncClient

    from app.database.session import get_db
    from app.main import app

    async def _override_get_db():
        async with pilot_seeded_postgres() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
    app.dependency_overrides.clear()


async def _register_device(client, device_id: str) -> dict:
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['accessToken']}"}


@_requires_postgres_url
@pytest.mark.asyncio
async def test_real_import_against_real_postgres_connection(pilot_seeded_postgres):
    """Sanity check the fixture itself actually used asyncpg, not SQLite,
    and that the three fixed defects hold against a real connection."""
    from app.models.ingredient import Ingredient
    from app.models.ingredient_localization import IngredientLocalization
    from app.services.ingredient_localization import build_localizations

    async with pilot_seeded_postgres() as db:
        assert "asyncpg" in str(db.bind.url)

        e150d = await db.get(Ingredient, "e150d_sulphite_ammonia_caramel")
        assert e150d is not None
        loc = build_localizations(e150d)
        # Defect 3: BG common name localized, not English.
        assert loc["bg"]["commonName"] == "Сулфитно-амонячен карамел"

        bg_e951 = await db.get(IngredientLocalization, ("e951_aspartame", "bg"))
        assert bg_e951.owner_approved_without_review is True
        assert bg_e951.translation_status.value == "DRAFT"


@_requires_postgres_url
@pytest.mark.asyncio
async def test_barcode_discovery_response_serves_owner_approved_pilot_content(
    pg_app_client, pilot_seeded_postgres, monkeypatch
):
    """Real POST /api/v1/scan/barcode path, external provider mocked (no
    network), against a real Postgres-backed app."""
    from app.core.config import settings
    from app.integrations.barcode_providers.base import (
        BarcodeProductProvider,
        NutritionFacts,
        ProviderMetadata,
        ProviderProductResult,
    )
    from app.services import barcode_discovery

    monkeypatch.setattr(settings, "BARCODE_DISCOVERY_ENABLED", True)
    monkeypatch.setattr(settings, "OPEN_FOOD_FACTS_ENABLED", True)
    monkeypatch.setattr(settings, "GS1_RESOLVER_ENABLED", False)
    monkeypatch.setattr(settings, "UPCITEMDB_ENABLED", False)

    class _FakeProvider(BarcodeProductProvider):
        def __init__(self, name: str, trust: float, outcome):
            self.metadata = ProviderMetadata(name=name, base_trust=trust)
            self._outcome = outcome

        async def fetch(self, barcode):
            return self._outcome

    off_result = ProviderProductResult(
        provider="open_food_facts",
        external_id="pg-pilot-ext-1",
        product_name="PG Pilot Test Cola",
        brand="PGPilotBrand",
        category="Sodas",
        image_url=None,
        raw_ingredient_text="Carbonated water, Sugar, Caramel colour (E150d)",
        nutrition=NutritionFacts(sugar_grams=10.0, sodium_mg=5.0, saturated_fat_grams=0.0, nova_group=4),
        allergens=[],
        dietary_flags={},
    )
    monkeypatch.setattr(
        barcode_discovery, "OpenFoodFactsProvider", lambda: _FakeProvider("open_food_facts", 0.75, off_result)
    )

    headers = await _register_device(pg_app_client, "pg-pilot-barcode-device")
    barcode = "4006381333931"  # real, checksum-valid EAN-13, not pre-seeded
    resp = await pg_app_client.post("/api/v1/scan/barcode", json={"barcode": barcode}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ingredients = body["product"]["ingredients"] if "ingredients" in body.get("product", {}) else body["ingredients"]
    matched = [i for i in ingredients if i.get("eNumber") == "E150d"]
    assert matched, [i.get("eNumber") or i.get("commonName") for i in ingredients]
    bg = matched[0]["localizations"]["bg"]
    assert bg["commonName"] == "Сулфитно-амонячен карамел"
    assert bg["translationStatus"] == "DRAFT"
    assert bg["ownerApprovedWithoutReview"] is True


@_requires_postgres_url
@pytest.mark.asyncio
async def test_label_image_scan_response_serves_owner_approved_pilot_content(pg_app_client, monkeypatch):
    """Real POST /api/v1/scan/label-image path, Gemini mocked (no
    network), against a real Postgres-backed app."""
    from app.integrations.gemini import gemini_service

    payload = {
        "productName": "PG Pilot Test Soft Drink",
        "brand": "PGPilotBrand",
        "sugarGrams": 10.0,
        "sodiumMg": 5.0,
        "saturatedFatGrams": 0.0,
        "nutritionBasis": "PER_100G",
        "hasArtificialSweeteners": False,
        "hasPreservatives": False,
        "isGlutenFree": False,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 4,
        "rawIngredientText": "Carbonated water, Sugar, Citric acid (E330)",
        "ingredients": [{"commonName": "Citric acid", "eNumber": "E330"}],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(payload)

    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    headers = await _register_device(pg_app_client, "pg-pilot-label-image-device")
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(4, 5, 6)).save(buf, format="JPEG")
    files = {"image": ("label.jpg", buf.getvalue(), "image/jpeg")}

    resp = await pg_app_client.post("/api/v1/scan/label-image", headers=headers, files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    matched = [i for i in body["ingredients"] if i.get("eNumber") == "E330"]
    assert matched, [i.get("eNumber") or i.get("commonName") for i in body["ingredients"]]
    assert matched[0]["id"] == "e330_citric_acid"
    bg = matched[0]["localizations"]["bg"]
    # Defect 3, the exact reproduced case: used to be "Citric acid".
    assert bg["commonName"] == "Лимонена киселина"
    assert bg["commonName"] != "Citric acid"
