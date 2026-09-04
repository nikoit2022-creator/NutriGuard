"""
Coverage for `app.seed.load_seed`'s persistent-ingredient-knowledge-
cache additions: every curated row gets real provenance
(VERIFIED/CURATED_SEED/full confidence) instead of the fail-safe column
defaults, a derived INS number where a genuine E-number exists, and its
own name plus the small curated Bulgarian/spelling-variant list
registered as aliases (task: "persistent ingredient knowledge cache",
requirement 2).
"""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import IngredientSource, IngredientVerificationStatus
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.repositories import ingredient_alias_repository
from app.seed import load_seed as load_seed_module


@pytest.mark.asyncio
async def test_load_seed_gives_every_curated_row_real_provenance(db_engine, monkeypatch):
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", session_factory)

    count = await load_seed_module.load_seed()
    assert count == 12

    async with session_factory() as db:
        aspartame = await db.get(Ingredient, "e951_aspartame")
        assert aspartame.verification_status == IngredientVerificationStatus.VERIFIED
        assert aspartame.source == IngredientSource.CURATED_SEED
        assert aspartame.risk_assessment_available is True
        assert float(aspartame.confidence) == 1.0
        assert aspartame.normalized_name == "aspartame"
        assert aspartame.ins_number == "951"  # derived from E951, never fabricated
        assert aspartame.last_verified_at is not None

        # No E-number -> no INS number guessed.
        oat_flour = await db.get(Ingredient, "whole_oat_flour")
        assert oat_flour.ins_number is None


@pytest.mark.asyncio
async def test_load_seed_registers_each_row_own_name_and_curated_variants_as_aliases(db_engine, monkeypatch):
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", session_factory)

    await load_seed_module.load_seed()

    async with session_factory() as db:
        own_name = await ingredient_alias_repository.get_by_normalized(db, "aspartame")
        assert own_name is not None
        assert own_name.ingredient_id == "e951_aspartame"

        bulgarian = await ingredient_alias_repository.get_by_normalized(db, "аспартам")
        assert bulgarian is not None
        assert bulgarian.ingredient_id == "e951_aspartame"
        assert bulgarian.language == "bg"

        spelling_variant = await ingredient_alias_repository.get_by_normalized(db, "aspartam")
        assert spelling_variant is not None
        assert spelling_variant.ingredient_id == "e951_aspartame"

        # Every one of the 12 curated rows has at least its own name
        # registered -- no silently-unresolvable curated ingredient.
        rows = (await db.execute(select(Ingredient))).scalars().all()
        for row in rows:
            alias = await ingredient_alias_repository.get_by_normalized(db, row.normalized_name)
            assert alias is not None and alias.ingredient_id == row.id, row.id


@pytest.mark.asyncio
async def test_load_seed_is_idempotent_and_never_duplicates_aliases(db_engine, monkeypatch):
    """Re-running the loader (e.g. every container restart, per
    CLAUDE.md) must not create duplicate ingredient or alias rows."""
    session_factory = async_sessionmaker(bind=db_engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(load_seed_module, "AsyncSessionLocal", session_factory)

    await load_seed_module.load_seed()
    await load_seed_module.load_seed()

    async with session_factory() as db:
        ingredient_rows = (await db.execute(select(Ingredient))).scalars().all()
        assert len(ingredient_rows) == 12

        alias_rows = (await db.execute(select(IngredientAlias))).scalars().all()
        # 12 self-aliases + the curated extra-variant list, each exactly once.
        assert len(alias_rows) == 12 + len(load_seed_module._EXTRA_ALIASES)
