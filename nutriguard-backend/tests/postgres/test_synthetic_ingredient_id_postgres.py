"""
Real PostgreSQL regression test for PR #13 review finding 1: every
generated synthetic ingredient id must fit `Ingredient.id`'s real
`String(64)` column -- the ONLY way to prove that conclusively is an
actual INSERT against a real Postgres instance (SQLite, this project's
default test-suite DB, does not enforce `VARCHAR` length limits at
all, so a >64-character id would silently "succeed" there even though
it fails outright against Postgres/production).

Deliberately NOT part of the default `pytest -q` run -- same opt-in
convention as the sibling files in this directory (see e.g.
`test_ingredient_catalog_concurrency_postgres.py`'s docstring for how
to run this one: same `NUTRIGUARD_TEST_POSTGRES_URL` env var).

Covers the exact categories the review asked for: very long ASCII,
Bulgarian/Unicode, punctuation-heavy, and colliding-prefix names.
"""
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
async def test_long_unicode_and_punctuation_heavy_names_insert_cleanly_into_real_postgres(postgres_url):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.ingredient import Ingredient
    from app.services.ocr_normalizer import create_synthetic_ingredient
    from app.services.ingredient_catalog import get_or_create_catalog_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    # Each name stays under `Ingredient.common_name`'s own String(255)
    # limit (a separate column, not under test here) while comfortably
    # exceeding the 64-character `id` limit that IS under test, so
    # truncation is actually exercised.
    names = {
        "very_long_ascii": (
            "PG Test Modified Corn Starch Hydrolysate Emulsifier Stabilizer Thickening Agent "
            "Preservative Blend Batch Twelve " * 2
        ),
        "bulgarian_unicode": (
            "PG Test Модифицирано царевично нишесте хидролизат емулгатор стабилизатор "
            "сгъстител консервант партиден " * 2
        ),
        "punctuation_heavy": "PG Test !!!Sodium-Benzoate/Potassium.Sorbate (E211/E202) -- 99.9%!!! @@@###$$$%%%^^^&&&***",
        "colliding_prefix_a": ("PG Test Highly Specific Compound Additive Formulation Batch " * 2) + "Variant Alpha",
        "colliding_prefix_b": ("PG Test Highly Specific Compound Additive Formulation Batch " * 2) + "Variant Beta",
    }
    for name in names.values():
        assert len(name) <= 255, f"test fixture name itself exceeds common_name's String(255): {len(name)} chars"
    synthetics = {key: create_synthetic_ingredient(name) for key, name in names.items()}

    ids = [s.id for s in synthetics.values()]
    assert len(set(ids)) == len(ids), f"expected all distinct ids, got {ids!r}"
    for ident in ids:
        assert len(ident) <= 64, f"id {ident!r} ({len(ident)} chars) exceeds Ingredient.id's String(64) limit"

    setup_session = session_factory()
    try:
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_(ids)))
        await setup_session.commit()
    finally:
        await setup_session.close()

    session = session_factory()
    try:
        resolved = {}
        for key, synthetic in synthetics.items():
            resolved[key] = await get_or_create_catalog_ingredient(session, synthetic)
        await session.commit()
    finally:
        await session.close()

    verify_session = session_factory()
    try:
        for key, synthetic in synthetics.items():
            row = (
                await verify_session.execute(select(Ingredient).where(Ingredient.id == synthetic.id))
            ).scalar_one_or_none()
            assert row is not None, f"expected a persisted row for {key} (id={synthetic.id!r})"
            assert row.id == resolved[key].id
            assert len(row.id) <= 64

        # The colliding-prefix pair must never have been merged into one
        # row by the (deliberately truncated, non-unique-by-itself) slug
        # -- only the content hash disambiguates them.
        assert resolved["colliding_prefix_a"].id != resolved["colliding_prefix_b"].id
    finally:
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_(ids)))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()
