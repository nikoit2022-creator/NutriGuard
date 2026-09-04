"""
Real concurrent-session PostgreSQL test for the persistent ingredient
knowledge cache (task: "persistent ingredient knowledge cache",
requirement 9: "Add a real PostgreSQL concurrency regression test if
the database schema changes" -- it did, see migration `e4f5a6b7c8d9`).

Deliberately NOT part of the default `pytest -q` run -- same opt-in
convention as `tests/postgres/test_concurrent_enrichment_postgres.py`
(see that file's docstring for the full rationale and how to run this
one: same `NUTRIGUARD_TEST_POSTGRES_URL` env var).

Proves the SAVEPOINT get-or-create pattern in
`ingredient_repository.insert_new`/`ingredient_alias_repository.get_or_create`
(the exact same pattern already proven for `Product`/`ProductSource` in
the sibling file) also holds for a genuinely new ingredient: two
concurrent sessions resolving the SAME never-before-seen ingredient
name must converge on exactly one `Ingredient` row and one
`IngredientAlias` row, never a duplicate, and neither call may raise.
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres instance to run this test.",
)


@pytest.fixture(scope="module")
def postgres_url() -> str:
    return os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]


@pytest.mark.asyncio
async def test_concurrent_resolution_of_the_same_new_ingredient_converges_on_one_row(postgres_url):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.services import ingredient_catalog
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    # A name not used by any other test in this file/module, to keep a
    # re-run against the same disposable database clean.
    ingredient_name = "Concurrent Catalog Test Additive"
    expected_id = create_synthetic_ingredient(ingredient_name).id

    setup_session = session_factory()
    try:
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id == expected_id))
        await setup_session.commit()
    finally:
        await setup_session.close()

    # `get_or_create_catalog_ingredient` is a lower-level service call
    # meant to be composed inside a LARGER request-scoped transaction
    # that commits once at the end (exactly how `food_analysis.py`
    # calls it via `materialize_ingredients` -- see that module's own
    # "exactly ONE db.commit()" transaction-boundary convention). Each
    # concurrent "request" here must therefore commit ITS OWN work
    # before returning, precisely like a real request would -- deferring
    # both commits until AFTER `asyncio.gather` (as an earlier draft of
    # this test did) self-deadlocks: the second session's INSERT blocks
    # on Postgres waiting for the first session's transaction to
    # resolve (commit or rollback), but that commit was scheduled to
    # happen only after `gather` returns, which itself never happens
    # while the second call is still blocked -- neither coroutine can
    # make progress. Wrapping each side in its own resolve-then-commit
    # coroutine (mirroring the sibling file's full `analyze_label_image_with_barcode(...)`
    # calls, which already commit internally) avoids that entirely.
    async def _resolve_and_commit(session):
        resolved = await ingredient_catalog.get_or_create_catalog_ingredient(
            session, create_synthetic_ingredient(ingredient_name)
        )
        await session.commit()
        return resolved

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            _resolve_and_commit(session_a),
            _resolve_and_commit(session_b),
            return_exceptions=True,
        )
    finally:
        await session_a.close()
        await session_b.close()

    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent ingredient resolution raised: {result!r}") from result
        assert result.id == expected_id

    verify_session = session_factory()
    try:
        ingredient_count = (
            await verify_session.execute(
                select(func.count()).select_from(Ingredient).where(Ingredient.id == expected_id)
            )
        ).scalar_one()
        assert ingredient_count == 1, f"expected exactly one Ingredient row for {expected_id!r}, found {ingredient_count}"

        alias_count = (
            await verify_session.execute(
                select(func.count()).select_from(IngredientAlias).where(IngredientAlias.ingredient_id == expected_id)
            )
        ).scalar_one()
        assert alias_count == 1, f"expected exactly one IngredientAlias row for {expected_id!r}, found {alias_count}"
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.ingredient_id == expected_id)
        )
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id == expected_id))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_resolution_of_the_same_new_e_number_from_different_display_names_converges(postgres_url):
    """Two concurrent OCR observations of the SAME never-before-seen
    E-number but under DIFFERENT display text (different normalized
    name, different deterministic id/slug -- e.g. "Vitamin C (E300)"
    vs. "Ascorbic Acid (E300)") must still converge on exactly one
    canonical `Ingredient` row, since `e_number` is the strongest
    identity key (task requirement 2) -- not two rows racing to claim
    the same E-number."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.ingredient import Ingredient
    from app.services import ingredient_catalog
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    name_a = "Vitamin C Test (E300)"
    name_b = "Ascorbic Acid Test (E300)"
    synthetic_a = create_synthetic_ingredient(name_a)
    synthetic_b = create_synthetic_ingredient(name_b)
    assert synthetic_a.e_number == "E300"
    assert synthetic_b.e_number == "E300"
    assert synthetic_a.id != synthetic_b.id  # different display text -> different deterministic ids

    setup_session = session_factory()
    try:
        await setup_session.execute(
            Ingredient.__table__.delete().where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
        )
        await setup_session.commit()
    finally:
        await setup_session.close()

    async def _resolve_and_commit(session, synthetic):
        resolved = await ingredient_catalog.get_or_create_catalog_ingredient(session, synthetic)
        await session.commit()
        return resolved

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            _resolve_and_commit(session_a, synthetic_a),
            _resolve_and_commit(session_b, synthetic_b),
            return_exceptions=True,
        )
    finally:
        await session_a.close()
        await session_b.close()

    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent E-number resolution raised: {result!r}") from result

    ids = {r.id for r in results}
    assert len(ids) == 1, f"expected both concurrent calls to converge on ONE id, got {ids!r}"

    verify_session = session_factory()
    try:
        e_number_count = (
            await verify_session.execute(
                select(func.count()).select_from(Ingredient).where(Ingredient.e_number == "E300")
            )
        ).scalar_one()
        assert e_number_count == 1, f"expected exactly one row for E300, found {e_number_count}"
    finally:
        await verify_session.execute(
            Ingredient.__table__.delete().where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
        )
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()
