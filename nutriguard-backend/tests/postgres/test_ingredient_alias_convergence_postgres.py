"""
Real concurrent-session PostgreSQL regression test for PR #13 review
finding 3 ("canonical alias convergence"): `ingredient_alias_repository.
get_or_create()` can return an EXISTING alias owned by a DIFFERENT
ingredient than the caller's own candidate row, and both call sites in
`ingredient_catalog.get_or_create_catalog_ingredient` used to ignore
that returned owner entirely -- returning their own (possibly orphaned)
candidate regardless. Fixed by `_register_alias_and_resolve_canonical`.

Deliberately NOT part of the default `pytest -q` run -- same opt-in
convention as `tests/postgres/test_ingredient_catalog_concurrency_postgres.py`
(see that file's docstring for the full rationale and how to run this
one: same `NUTRIGUARD_TEST_POSTGRES_URL` env var).

Unlike the sibling concurrency file's own alias-adjacent coverage (two
concurrent scans of the exact SAME name, or two names sharing the same
E-number), this test deliberately uses two DIFFERENT candidate
ingredient ids with NO E-number at all, that both normalize to the SAME
alias text -- e.g. a trailing-punctuation OCR variant ("Additive." vs.
"Additive") -- so `ocr_normalizer._synthetic_id`'s content hash (which
covers the untruncated, unnormalized name) gives them genuinely
different primary keys while `ingredient_normalization.
normalize_ingredient_name` still collapses them to one alias. This is
exactly the race `get_by_official_identifier` (E-number) can't help
resolve, so it exercises the alias-table convergence path specifically.
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
async def test_concurrent_different_ids_normalizing_to_the_same_alias_converge_on_one_canonical_row(postgres_url):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.services import ingredient_catalog
    from app.services.ingredient_normalization import normalize_ingredient_name
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    # Same real-world ingredient, two different OCR-observed display
    # strings (a trailing-period variant) -- no E-number in either, so
    # `get_by_official_identifier` can never converge them; only the
    # alias table can. `_synthetic_id`'s content hash covers the FULL,
    # untruncated name (including the trailing period), so these two
    # deterministically produce DIFFERENT ids even though they
    # normalize to the exact same alias text.
    name_a = "Alias Convergence Test Additive."
    name_b = "Alias Convergence Test Additive"
    synthetic_a = create_synthetic_ingredient(name_a)
    synthetic_b = create_synthetic_ingredient(name_b)
    assert synthetic_a.e_number is None
    assert synthetic_b.e_number is None
    assert synthetic_a.id != synthetic_b.id
    normalized = normalize_ingredient_name(name_a)
    assert normalized == normalize_ingredient_name(name_b)

    setup_session = session_factory()
    try:
        await setup_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await setup_session.execute(
            Ingredient.__table__.delete().where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
        )
        await setup_session.commit()
    finally:
        await setup_session.close()

    # Same "each concurrent request commits its own work" shape as the
    # sibling concurrency file -- see that file's docstring for why
    # deferring both commits until after `asyncio.gather` would
    # self-deadlock.
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
            raise AssertionError(f"concurrent alias-convergence resolution raised: {result!r}") from result

    ids = {r.id for r in results}
    assert len(ids) == 1, f"expected both concurrent calls to converge on ONE canonical ingredient, got {ids!r}"
    canonical_id = ids.pop()
    assert canonical_id in (synthetic_a.id, synthetic_b.id)

    verify_session = session_factory()
    try:
        # Exactly one canonical row -- the race's LOSER's own row must
        # not be left behind as an orphan duplicate.
        ingredient_count = (
            await verify_session.execute(
                select(func.count())
                .select_from(Ingredient)
                .where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
            )
        ).scalar_one()
        assert ingredient_count == 1, (
            f"expected exactly one surviving Ingredient row for {[synthetic_a.id, synthetic_b.id]!r}, "
            f"found {ingredient_count} -- the alias race's loser must be deleted, not left as an orphan"
        )
        surviving_id = (
            await verify_session.execute(
                select(Ingredient.id).where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
            )
        ).scalar_one()
        assert surviving_id == canonical_id

        # Exactly one alias row for this normalized text, pointing at
        # the same canonical row every concurrent caller resolved to.
        aliases = (
            await verify_session.execute(
                select(IngredientAlias).where(IngredientAlias.alias_normalized == normalized)
            )
        ).scalars().all()
        assert len(aliases) == 1, f"expected exactly one IngredientAlias row for {normalized!r}, found {len(aliases)}"
        assert aliases[0].ingredient_id == canonical_id
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await verify_session.execute(
            Ingredient.__table__.delete().where(Ingredient.id.in_([synthetic_a.id, synthetic_b.id]))
        )
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()
