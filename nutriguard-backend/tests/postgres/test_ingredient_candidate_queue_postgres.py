"""
Real concurrent-session PostgreSQL tests for the ingredient candidate queue
(issue #23, stage 2; migration `d7e8f9a0b1c2`). Opt-in, like the sibling
files in this directory: set `NUTRIGUARD_TEST_POSTGRES_URL` to a disposable
instance already migrated to head.

Proves, with genuinely separate connections/transactions:
  * many concurrent scans of the same NEW token converge on exactly one row
    whose `encounter_count` equals the number of scans (no lost increment,
    no duplicate, no error);
  * concurrent scans of the same EXISTING token each add exactly one;
  * scans touching the same keys in opposite orders do not deadlock (keys
    are locked in sorted order);
  * `ON DELETE SET NULL` keeps an observation when its identity is deleted;
  * the CHECK constraints reject an unknown status and a non-positive count.
"""
import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres instance to run this test.",
)

PREFIX = "pgq-test-"


@pytest.fixture(scope="module")
def postgres_url() -> str:
    return os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]


@pytest.fixture
async def factory(postgres_url):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.ingredient_candidate import IngredientCandidate

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    async def clean():
        async with session_factory() as session:
            await session.execute(
                IngredientCandidate.__table__.delete().where(IngredientCandidate.normalized_key.like(f"{PREFIX}%"))
            )
            await session.commit()

    await clean()
    yield session_factory
    await clean()
    await engine.dispose()


async def _observe(session_factory, names: list[str]) -> None:
    from app.services import ingredient_candidates

    async with session_factory() as session:  # one session = one scan request
        await ingredient_candidates.record_observations(
            session, [ingredient_candidates.Observation(name=n, e_number=None) for n in names]
        )
        await session.commit()


async def _row(session_factory, key: str):
    from sqlalchemy import select

    from app.models.ingredient_candidate import IngredientCandidate

    async with session_factory() as session:
        return (
            await session.execute(select(IngredientCandidate).where(IngredientCandidate.normalized_key == key))
        ).scalar_one_or_none()


@pytest.mark.asyncio
async def test_concurrent_scans_of_a_new_token_converge_on_one_row_with_every_encounter_counted(factory):
    name = f"{PREFIX}new-token"
    await asyncio.gather(*[_observe(factory, [name]) for _ in range(12)])

    from sqlalchemy import func, select

    from app.models.ingredient_candidate import IngredientCandidate

    async with factory() as session:
        count = (
            await session.execute(
                select(func.count()).select_from(IngredientCandidate).where(IngredientCandidate.normalized_key == name)
            )
        ).scalar_one()
    row = await _row(factory, name)
    assert count == 1
    assert row.encounter_count == 12  # no lost increment
    assert row.first_seen_at <= row.last_seen_at


@pytest.mark.asyncio
async def test_concurrent_scans_of_an_existing_token_each_add_exactly_one(factory):
    name = f"{PREFIX}existing-token"
    await _observe(factory, [name])
    await asyncio.gather(*[_observe(factory, [name]) for _ in range(10)])
    assert (await _row(factory, name)).encounter_count == 11


@pytest.mark.asyncio
async def test_scans_touching_the_same_keys_in_opposite_orders_do_not_deadlock(factory):
    keys = [f"{PREFIX}k{i}" for i in range(6)]
    for key in keys:
        await _observe(factory, [key])
    forward, backward = list(keys), list(reversed(keys))

    results = await asyncio.wait_for(
        asyncio.gather(
            *[_observe(factory, forward if i % 2 == 0 else backward) for i in range(8)], return_exceptions=True
        ),
        timeout=60,
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    for key in keys:
        assert (await _row(factory, key)).encounter_count == 1 + 8  # each of the 8 scans counted each key once


@pytest.mark.asyncio
async def test_deleting_an_identity_keeps_the_observation(factory):
    from sqlalchemy import select

    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient
    from app.models.ingredient_candidate import IngredientCandidate

    ingredient_id = f"{PREFIX}identity"
    key = f"{PREFIX}linked"
    async with factory() as session:
        session.add(Ingredient(
            id=ingredient_id, common_name="Pgq identity", normalized_name="pgq identity", scientific_name="",
            category="", description="", purpose_in_food="", health_concerns="", evidence_level="",
            countries_restricted_or_banned="", efsa_status="", fda_status="", acceptable_daily_intake="",
            side_effects="", allergens="", references="", risk_level=RiskLevel.SAFE, risk_assessment_available=False,
            verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC,
            confidence=0.2,
        ))
        await session.flush()
        session.add(IngredientCandidate(
            normalized_key=key, display_name="Pgq linked", ingredient_id=ingredient_id, status="PENDING",
            flags="", encounter_count=1,
        ))
        await session.commit()
    try:
        async with factory() as session:
            await session.execute(Ingredient.__table__.delete().where(Ingredient.id == ingredient_id))
            await session.commit()
        row = await _row(factory, key)
        assert row is not None and row.ingredient_id is None  # ON DELETE SET NULL
    finally:
        async with factory() as session:
            await session.execute(Ingredient.__table__.delete().where(Ingredient.id == ingredient_id))
            await session.commit()


@pytest.mark.asyncio
async def test_check_constraints_reject_an_unknown_status_and_a_non_positive_count(factory):
    from sqlalchemy.exc import IntegrityError

    from app.models.ingredient_candidate import IngredientCandidate

    for bad in (
        dict(status="BOGUS", encounter_count=1),
        dict(status="PENDING", encounter_count=0),
    ):
        async with factory() as session:
            session.add(IngredientCandidate(normalized_key=f"{PREFIX}bad", display_name="bad", flags="", **bad))
            with pytest.raises(IntegrityError):
                await session.commit()
