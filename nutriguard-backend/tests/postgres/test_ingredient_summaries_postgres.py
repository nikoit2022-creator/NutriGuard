"""
Real concurrent-session PostgreSQL tests for the ingredient summaries
(issue #23, stage 3; migration `e8f9a0b1c2d3`). Opt-in, like the sibling
files in this directory: set `NUTRIGUARD_TEST_POSTGRES_URL` to a disposable
instance already migrated to head.

Proves, with genuinely separate connections/transactions:
  * many loaders started at once converge on exactly one summary row and one
    translation row per subject, with no error and the creation counted once
    (the savepoint-and-re-read path of `load_summaries`);
  * a repeat load is a no-op;
  * the CHECK constraints reject an unknown scope, evidence state, translation
    status/source and language, the unique key rejects a duplicate subject,
    and deleting a summary deletes its translations.
"""
import asyncio
import copy
import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres instance to run this test.",
)


@pytest.fixture(scope="module")
def postgres_url() -> str:
    return os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]


@pytest.fixture
async def factory(postgres_url):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    yield session_factory
    await engine.dispose()


@pytest.fixture
def content_file(tmp_path):
    """A one-subject copy of the pilot content under a unique, test-only key."""
    from app.seed import load_summaries as loader

    data = json.loads(loader.SUMMARIES_SEED_FILE.read_text(encoding="utf-8"))
    subject = copy.deepcopy(next(s for s in data["subjects"] if s["subjectKey"] == "name:salt"))
    subject["subjectKey"] = f"name:pgsum-test-{uuid.uuid4().hex[:10]}"
    data["subjects"] = [subject]
    path = tmp_path / "pg_summaries.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    yield path, subject["subjectKey"]


async def _cleanup(factory, key: str) -> None:
    from sqlalchemy import delete

    from app.models.ingredient_summary import IngredientSummary

    async with factory() as session:
        await session.execute(delete(IngredientSummary).where(IngredientSummary.subject_key == key))
        await session.commit()


async def _load(factory, path):
    from app.seed import load_summaries as loader

    async with factory() as session:
        counters = await loader.load_summaries(session, path)
        await session.commit()
    return counters


async def _count(factory, key: str) -> tuple[int, int]:
    from sqlalchemy import func, select

    from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization

    async with factory() as session:
        summaries = (
            await session.execute(
                select(func.count()).select_from(IngredientSummary).where(IngredientSummary.subject_key == key)
            )
        ).scalar_one()
        translations = (
            await session.execute(
                select(func.count())
                .select_from(IngredientSummaryLocalization)
                .join(IngredientSummary, IngredientSummary.id == IngredientSummaryLocalization.summary_id)
                .where(IngredientSummary.subject_key == key)
            )
        ).scalar_one()
    return summaries, translations


@pytest.mark.asyncio
async def test_concurrent_loaders_converge_on_one_row_per_subject_and_count_the_creation_once(factory, content_file):
    path, key = content_file
    try:
        results = await asyncio.gather(*[_load(factory, path) for _ in range(8)], return_exceptions=True)
        errors = [r for r in results if isinstance(r, BaseException)]
        assert not errors, errors
        assert await _count(factory, key) == (1, 1)
        assert sum(r["created"] for r in results) == 1
        assert sum(r["translations_created"] for r in results) == 1
        again = await _load(factory, path)
        assert again["created"] == 0 and again["updated"] == 0 and again["unchanged"] == 1
        assert await _count(factory, key) == (1, 1)
    finally:
        await _cleanup(factory, key)


@pytest.mark.asyncio
async def test_the_database_rejects_invalid_rows_and_cascades_translation_deletes(factory, content_file):
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization

    path, key = content_file
    await _load(factory, path)
    try:
        async with factory() as session:
            row = (await session.execute(select(IngredientSummary).where(IngredientSummary.subject_key == key))).scalar_one()
            digest, summary_id = row.content_hash, row.id

        def _summary(**overrides):
            values = dict(
                subject_key=f"name:pgsum-bad-{uuid.uuid4().hex[:8]}",
                scope="INGREDIENT_NAME",
                evidence_state="SOURCE_VERIFIED",
                content_hash=digest,
            )
            values.update(overrides)
            return IngredientSummary(**values)

        for bad in ({"scope": "FAMILY"}, {"evidence_state": "VERIFIED"}, {"subject_key": key}):
            async with factory() as session:
                session.add(_summary(**bad))
                with pytest.raises(IntegrityError):
                    await session.commit()

        # Free the (summary, 'bg') key so each rejection below is the CHECK, not a duplicate key.
        async with factory() as session:
            await session.execute(
                IngredientSummaryLocalization.__table__.delete().where(
                    IngredientSummaryLocalization.summary_id == summary_id
                )
            )
            await session.commit()
        for bad in ({"translation_status": "APPROVED"}, {"translation_source": "AI"}, {"language": "fr"}):
            async with factory() as session:
                values = dict(
                    summary_id=summary_id, language="bg", translation_status="DRAFT",
                    translation_source="MACHINE_TRANSLATED", source_content_hash=digest,
                )
                values.update(bad)
                session.add(IngredientSummaryLocalization(**values))
                with pytest.raises(IntegrityError):
                    await session.commit()

        # Deleting the summary removes its translations (ON DELETE CASCADE).
        await _load(factory, path)  # re-creates the summary and its translation
        assert await _count(factory, key) == (1, 1)
        await _cleanup(factory, key)
        assert await _count(factory, key) == (0, 0)
    finally:
        await _cleanup(factory, key)
