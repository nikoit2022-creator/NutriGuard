"""Exercise the owner's actual read-only export against disposable PostgreSQL."""
import json
import os
import subprocess
import sys

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_candidate import IngredientCandidate
from app.seed.ingredient_review_list import read_report
from app.services import ingredient_review_list
from app.services.ingredient_catalog import materialize_ingredients
from app.services.ocr_normalizer import create_synthetic_ingredient


pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Requires a disposable PostgreSQL instance migrated to head.",
)


@pytest.mark.asyncio
async def test_cli_bilingual_identity_export_is_read_only_and_preserves_snapshot(tmp_path, monkeypatch):
    database_url = os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    monkeypatch.setattr("app.services.ingredient_catalog.detect_language", lambda value: "en")
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ingredient_id = None
    try:
        async with factory() as session:
            rows, _, _ = await materialize_ingredients(
                session, [create_synthetic_ingredient("Review export flour")]
            )
            ingredient_id = rows[0].id
            english_alias = (await session.execute(
                select(IngredientAlias).where(IngredientAlias.ingredient_id == ingredient_id)
            )).scalar_one()
            english_alias.language = "en"
            session.add(IngredientAlias(
                ingredient_id=ingredient_id, alias_text="Брашно за преглед",
                alias_normalized="брашно за преглед", language="bg", source=english_alias.source,
            ))
            session.add(IngredientCandidate(
                ingredient_id=ingredient_id, normalized_key="брашно за преглед",
                display_name="Брашно за преглед", status="PENDING", flags="", encounter_count=3,
            ))
            await session.commit()

        async def snapshot():
            async with engine.connect() as connection:
                return {
                    table: (await connection.execute(text(
                        f"SELECT row_to_json(records)::text FROM {table} records ORDER BY 1"
                    ))).scalars().all()
                    for table in (
                        "ingredients", "ingredient_aliases", "ingredient_localizations",
                        "ingredient_candidates", "products",
                    )
                }

        original_builder = ingredient_review_list.build_review_list

        async def checked_builder(session):
            assert (await session.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
            assert (await session.execute(text("SHOW transaction_isolation"))).scalar_one() == "repeatable read"
            assert (await session.execute(text("SHOW statement_timeout"))).scalar_one() == "15s"
            assert (await session.execute(text("SHOW lock_timeout"))).scalar_one() == "2s"
            return await original_builder(session)

        monkeypatch.setattr(ingredient_review_list, "build_review_list", checked_builder)
        before = await snapshot()
        report = await read_report()
        entries = [entry for entry in report["entries"] if entry["ingredientId"] == ingredient_id]
        assert len(entries) == 1
        assert (entries[0]["nameEn"], entries[0]["nameBg"]) == ("Review export flour", "Брашно за преглед")
        assert len(entries[0]["candidateIds"]) == 2
        assert entries[0]["tokenObservationCount"] == 4
        output = tmp_path / "review.json"
        command = [sys.executable, "-m", "app.seed.ingredient_review_list", "--output", str(output)]
        environment = dict(os.environ, DATABASE_URL=database_url)
        completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr
        assert json.loads(output.read_text(encoding="utf-8")) == report
        previous = output.read_bytes()
        failed_environment = dict(environment, DATABASE_URL="postgresql+asyncpg://invalid@127.0.0.1:1/missing")
        failed = subprocess.run(command, env=failed_environment, capture_output=True, text=True, timeout=30)
        assert failed.returncode == 1
        assert "Ingredient review export failed" in failed.stderr
        assert failed.stdout == ""
        assert output.read_bytes() == previous
        assert await snapshot() == before
    finally:
        if ingredient_id is not None:
            async with factory() as session:
                await session.execute(IngredientCandidate.__table__.delete().where(
                    IngredientCandidate.ingredient_id == ingredient_id
                ))
                await session.execute(Ingredient.__table__.delete().where(Ingredient.id == ingredient_id))
                await session.commit()
        await engine.dispose()
