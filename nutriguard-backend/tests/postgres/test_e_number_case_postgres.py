"""
Real PostgreSQL checks for the E-number case-normalization follow-up
(opt-in, same `NUTRIGUARD_TEST_POSTGRES_URL` convention as the sibling
files; the target database must already be migrated with
`alembic upgrade head` and disposable).

Covers what SQLite cannot: native enum columns in the trusted-source
ordering, the case-SENSITIVE unique index on `ingredients.e_number` that
let "E998D" and "E998d" coexist, `upper()` lookups against it, and that
the audit/plan scripts' `SET TRANSACTION READ ONLY` really rejects writes.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable, migrated Postgres instance.",
)

CURATED = "pgtest_e998d_curated"
OCR = "pgtest_e998d_ocr_twin"


def _ing(**kw):
    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient

    base = dict(
        common_name="x", normalized_name="x", scientific_name="", category="", description="",
        purpose_in_food="", health_concerns="", evidence_level="", countries_restricted_or_banned="",
        efsa_status="", fda_status="", acceptable_daily_intake="", side_effects="", allergens="",
        references="", risk_level=RiskLevel.SAFE, risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC,
        confidence=0.2,
    )
    base.update(kw)
    return Ingredient(**base)


@pytest.mark.asyncio
async def test_case_insensitive_lookup_and_read_only_guard_on_real_postgres():
    from sqlalchemy import delete, text
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.enums import IngredientSource, IngredientVerificationStatus
    from app.models.ingredient import Ingredient
    from app.repositories import ingredient_repository
    from scripts.plan_e_number_remediation import _load

    engine = create_async_engine(os.environ["NUTRIGUARD_TEST_POSTGRES_URL"], future=True)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with factory() as s:
            await s.execute(delete(Ingredient).where(Ingredient.id.in_([CURATED, OCR])))
            s.add_all(
                [
                    _ing(id=CURATED, common_name="PG Sulphite ammonia caramel", normalized_name="pg sulphite ammonia caramel",
                         e_number="E998d", ins_number="998d", source=IngredientSource.CURATED_SEED,
                         verification_status=IngredientVerificationStatus.LIMITED_DATA, confidence=0.9),
                    # the case-sensitive unique index allows this twin -- the live bug
                    _ing(id=OCR, common_name="PG Colorant: e998d", normalized_name="pg colorant: e998d",
                         e_number="E998D", ins_number="998D"),
                ]
            )
            await s.commit()

        async with factory() as s:
            for spelling in ("E998D", "E998d", "e998d"):
                found = await ingredient_repository.get_by_official_identifier(s, e_number=spelling)
                assert found.id == CURATED, spelling
            assert (await ingredient_repository.get_by_id_or_e_number(s, "e998D")).id == CURATED
            assert await ingredient_repository.get_by_official_identifier(s, e_number="E998a") is None

        async with factory() as s:
            await s.execute(text("SET TRANSACTION READ ONLY"))
            plan = await _load(s)
            ours = [c for c in plan["case_collisions"] if c["normalized_e_number"] == "E998D"]
            assert ours and ours[0]["canonical_target"]["id"] == CURATED
            assert ours[0]["fields_modified_on_target"] == []
            with pytest.raises(DBAPIError):  # Postgres refuses the write inside a READ ONLY transaction
                await s.execute(text("UPDATE ingredients SET description = 'x' WHERE id = :i"), {"i": CURATED})
            await s.rollback()
    finally:
        async with factory() as s:
            await s.execute(delete(Ingredient).where(Ingredient.id.in_([CURATED, OCR])))
            await s.commit()
        await engine.dispose()
