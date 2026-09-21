"""
Issue #21 follow-up (section 3): the curated E471 seed contradiction.

`e471_mono_diglycerides` (VERIFIED, curated) says in its OWN text that it
is "derived from animal or plant fats" and that "source verification [is]
required for Halal/Kosher" -- yet its per-ingredient flags claimed
`isVegan`/`isVegetarian`/`isHalal`/`isKosher` = true unconditionally.
Those four are source-dependent and must be UNKNOWN (null) unless a
verified source states otherwise; `isGluten`/`isLactose` (fat-derived, no
gluten/lactose in the molecule) are unaffected.

Also pins that a seed RELOAD never restores the unsupported claims -- in
particular on a database that still holds the old `true` values from a
previous seed run (the live-DB situation): `session.merge` of the
corrected row must clear them.
"""
import json
import re

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.ingredient import Ingredient
from app.seed import load_seed as load_seed_module

SOURCE_DEPENDENT_FLAGS = ("isVegan", "isVegetarian", "isHalal", "isKosher")
_SOURCE_DEPENDENT_TEXT = re.compile(
    r"animal or plant|animal or vegetable|source verification|verify (?:the )?source|depending on (?:the )?source",
    re.IGNORECASE,
)


def _seed_rows() -> list[dict]:
    return json.loads(load_seed_module._SEED_FILE.read_text(encoding="utf-8"))


def test_seed_file_e471_source_dependent_flags_are_null():
    e471 = next(row for row in _seed_rows() if row["id"] == "e471_mono_diglycerides")
    for flag in SOURCE_DEPENDENT_FLAGS:
        assert e471[flag] is None, flag
    # Own text still says why (this is the evidence the null rests on):
    assert "animal or plant" in e471["description"]
    assert "source verification required for Halal/Kosher" in e471["countriesRestrictedOrBanned"]
    # The molecule itself contains no gluten/lactose -- unchanged, not over-corrected:
    assert e471["isGluten"] is False
    assert e471["isLactose"] is False


def test_no_curated_row_claims_a_dietary_fact_its_own_text_says_is_source_dependent():
    """Generic guard for every curated row (not just E471): if a row's own
    description/allergens/restriction text says the animal-vs-plant/halal/
    kosher status depends on the source, none of the four source-dependent
    flags may be a hardcoded True."""
    offenders = []
    for row in _seed_rows():
        text = " ".join(str(row.get(key) or "") for key in ("description", "allergens", "countriesRestrictedOrBanned"))
        if _SOURCE_DEPENDENT_TEXT.search(text):
            offenders += [f"{row['id']}.{flag}" for flag in SOURCE_DEPENDENT_FLAGS if row.get(flag) is True]
    assert offenders == []


def test_seed_rows_only_use_true_false_or_null_for_dietary_flags():
    for row in _seed_rows():
        for flag in ("isGluten", "isLactose", *SOURCE_DEPENDENT_FLAGS):
            assert row[flag] is None or isinstance(row[flag], bool), (row["id"], flag)


@pytest.mark.asyncio
async def test_load_seed_writes_unknown_for_e471_and_reload_is_idempotent(db_engine, monkeypatch):
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", session_factory)

    await load_seed_module.load_seed()
    await load_seed_module.load_seed()  # every container restart re-runs the seed

    async with session_factory() as db:
        e471 = await db.get(Ingredient, "e471_mono_diglycerides")
        assert (e471.is_vegan, e471.is_vegetarian, e471.is_halal, e471.is_kosher) == (None, None, None, None)
        assert e471.is_gluten is False and e471.is_lactose is False
        # An unaffected curated row keeps its (unqualified) explicit claims:
        soy_lecithin = await db.get(Ingredient, "e322_soy_lecithin")
        assert soy_lecithin.is_vegan is True


@pytest.mark.asyncio
async def test_seed_reload_clears_the_legacy_true_claims_from_an_already_seeded_database(db_engine, monkeypatch):
    """The live database was seeded with the OLD JSON (true x4). Loading
    the corrected seed over it must reset them, not preserve them."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", session_factory)

    await load_seed_module.load_seed()
    async with session_factory() as db:
        e471 = await db.get(Ingredient, "e471_mono_diglycerides")
        e471.is_vegan = e471.is_vegetarian = e471.is_halal = e471.is_kosher = True  # legacy state
        await db.commit()

    await load_seed_module.load_seed()

    async with session_factory() as db:
        e471 = await db.get(Ingredient, "e471_mono_diglycerides")
        assert (e471.is_vegan, e471.is_vegetarian, e471.is_halal, e471.is_kosher) == (None, None, None, None)
