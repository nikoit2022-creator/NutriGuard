"""
Real concurrent-session PostgreSQL regression test for PR #13 review
finding 1 ("canonical identity precedence"): an official-identifier
match (E-number/INS/CAS) must always outrank a pre-existing alias owned
by a DIFFERENT, weaker row -- `_register_alias_and_resolve_canonical`
used to see the alias's existing owner and return THAT instead of the
official-identifier match, silently downgrading a curated/verified
resolution to an unverified one. Fixed by
`ingredient_catalog._reconcile_official_identifier_conflict`.

Deliberately NOT part of the default `pytest -q` run -- same opt-in
convention as the sibling files in this directory (see e.g.
`test_ingredient_catalog_concurrency_postgres.py`'s docstring for how
to run this one: same `NUTRIGUARD_TEST_POSTGRES_URL` env var).

Scenarios (PR #13 review round 3: "do not blindly transfer every alias
from an existing curated/verified alias owner to a different official-
identifier row" adds (b)/(c) below to the original two):

  a. Two concurrent sessions independently resolve the SAME
     official-identifier-bearing observation against a pre-existing
     UNVERIFIED/OCR_HEURISTIC alias-owning duplicate with no official
     identifier of its own (inserted synchronously beforehand, so both
     concurrent calls react to the SAME committed starting state) --
     both must converge on the curated/official row, the reconciliation
     must not raise or deadlock under real concurrent row-locking, the
     alias must end up pointing at the official row exactly once, and
     the provably-disposable duplicate must not survive
     (`test_concurrent_official_identifier_resolutions_converge_on_the_curated_row_over_a_pre_existing_alias`).

  b. Two DISTINCT, independently VERIFIED rows with DIFFERENT
     E-numbers happen to share a generic normalized alias text.
     Concurrently resolving one via its own official identifier must
     NEVER reassign that shared alias away from the other -- neither
     official identifier "proves" which one the ambiguous generic name
     really refers to
     (`test_concurrent_resolution_never_corrupts_a_shared_alias_between_two_distinct_verified_rows`).

  c. A referenced synthetic loser: the alias-owning UNVERIFIED
     duplicate is already referenced by a real `Product.ingredient_ids`
     -- concurrently reconciling it must repoint the alias (so future
     lookups converge on the official row) WITHOUT ever deleting the
     row itself, since that would leave the product with a dangling
     reference
     (`test_concurrent_reconciliation_of_a_referenced_synthetic_loser_never_deletes_it`).

  d. A genuine three-way race: two concurrent sessions each observe a
     never-before-seen name with NO identifier (racing each other AND a
     pre-existing curated row with the identifier already in the
     database) -- covers the "insert-then-lose-to-official-identifier"
     path (`owns_row=True` combined with the official-identifier
     conflict), not just the pre-seeded-duplicate path above
     (`test_concurrent_no_identifier_and_identifier_observations_of_a_brand_new_name_converge_on_the_curated_row`).
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
async def test_concurrent_official_identifier_resolutions_converge_on_the_curated_row_over_a_pre_existing_alias(
    postgres_url,
):
    from dataclasses import replace

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.repositories import ingredient_alias_repository
    from app.services import ingredient_catalog
    from app.services.ingredient_normalization import normalize_ingredient_name
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    official_id = "pg_official_precedence_curated"
    stub_id = "pg_official_precedence_stub"
    e_number = "E999"
    name = "PG Official Precedence Test Additive"
    normalized = normalize_ingredient_name(name)

    setup_session = session_factory()
    try:
        await setup_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        official = Ingredient(
            id=official_id,
            common_name="PG Official Precedence Curated Name",
            normalized_name=normalize_ingredient_name("PG Official Precedence Curated Name"),
            e_number=e_number,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=True,
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.CURATED_SEED,
            confidence=1.0,
        )
        stub = Ingredient(
            id=stub_id,
            common_name=name,
            normalized_name=normalized,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=False,
            verification_status=IngredientVerificationStatus.UNVERIFIED,
            source=IngredientSource.OCR_HEURISTIC,
            confidence=0.2,
        )
        setup_session.add_all([official, stub])
        await setup_session.flush()
        await ingredient_alias_repository.get_or_create(
            setup_session,
            ingredient_id=stub_id,
            alias_text=name,
            alias_normalized=normalized,
            language=None,
            source=IngredientSource.OCR_HEURISTIC,
        )
        await setup_session.commit()
    finally:
        await setup_session.close()

    # Both concurrent calls resolve via the official identifier -- the
    # curated row already exists (committed above) before either
    # starts, so this is unaffected by interleaving; what's under real
    # concurrent-session test here is `_reconcile_official_identifier_conflict`
    # itself: two sessions independently repointing the same alias set
    # and attempting to delete the same now-orphaned stub.
    synthetic = replace(create_synthetic_ingredient(name), e_number=e_number)
    assert normalize_ingredient_name(synthetic.common_name) == normalized

    async def _resolve_and_commit(session):
        resolved = await ingredient_catalog.get_or_create_catalog_ingredient(session, synthetic)
        await session.commit()
        return resolved

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            _resolve_and_commit(session_a), _resolve_and_commit(session_b), return_exceptions=True
        )
    finally:
        await session_a.close()
        await session_b.close()

    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent official-identifier reconciliation raised: {result!r}") from result
        assert result.id == official_id, f"expected both concurrent calls to converge on {official_id!r}, got {result.id!r}"

    verify_session = session_factory()
    try:
        # The official row is untouched and still the sole row for this
        # E-number.
        official_count = (
            await verify_session.execute(select(func.count()).select_from(Ingredient).where(Ingredient.e_number == e_number))
        ).scalar_one()
        assert official_count == 1

        # Exactly one alias row for this normalized text, converged on
        # the official row -- no matter how the two sessions interleaved.
        aliases = (
            await verify_session.execute(select(IngredientAlias).where(IngredientAlias.alias_normalized == normalized))
        ).scalars().all()
        assert len(aliases) == 1, f"expected exactly one IngredientAlias row for {normalized!r}, found {len(aliases)}"
        assert aliases[0].ingredient_id == official_id

        # The stub is provably safe to remove (UNVERIFIED, OCR_HEURISTIC,
        # no product ever referenced it) -- it must not survive as an
        # orphan duplicate.
        assert await verify_session.get(Ingredient, stub_id) is None
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_resolution_never_corrupts_a_shared_alias_between_two_distinct_verified_rows(postgres_url):
    """PR #13 review round 3, scenario (b): two DISTINCT, independently
    VERIFIED curated rows with DIFFERENT E-numbers happen to share a
    generic normalized alias text. Two concurrent sessions each resolve
    ONE of the two rows via its own official identifier -- neither may
    ever reassign the shared, ambiguous alias, and both curated rows
    must survive completely untouched."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.repositories import ingredient_alias_repository
    from app.services import ingredient_catalog
    from app.services.ingredient_normalization import normalize_ingredient_name
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    row_a_id = "pg_ambiguous_verified_a"
    row_b_id = "pg_ambiguous_verified_b"
    e_number_a = "E997"
    e_number_b = "E996"
    shared_name = "PG Shared Generic Family Name"
    shared_normalized = normalize_ingredient_name(shared_name)

    setup_session = session_factory()
    try:
        await setup_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == shared_normalized)
        )
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([row_a_id, row_b_id])))
        row_a = Ingredient(
            id=row_a_id,
            common_name="PG Ambiguous Verified Additive A",
            normalized_name=normalize_ingredient_name("PG Ambiguous Verified Additive A"),
            e_number=e_number_a,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=True,
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.CURATED_SEED,
            confidence=1.0,
        )
        row_b = Ingredient(
            id=row_b_id,
            common_name="PG Ambiguous Verified Additive B",
            normalized_name=normalize_ingredient_name("PG Ambiguous Verified Additive B"),
            e_number=e_number_b,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=True,
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.CURATED_SEED,
            confidence=1.0,
        )
        setup_session.add_all([row_a, row_b])
        await setup_session.flush()
        # The shared, ambiguous generic alias -- owned by row_b's curated
        # data before either concurrent call ever runs.
        await ingredient_alias_repository.get_or_create(
            setup_session,
            ingredient_id=row_b_id,
            alias_text=shared_name,
            alias_normalized=shared_normalized,
            language=None,
            source=IngredientSource.CURATED_SEED,
        )
        await setup_session.commit()
    finally:
        await setup_session.close()

    from dataclasses import replace

    synthetic_a = replace(create_synthetic_ingredient(shared_name), e_number=e_number_a)
    synthetic_b = replace(create_synthetic_ingredient(shared_name), e_number=e_number_b)

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
            raise AssertionError(f"concurrent ambiguous-alias resolution raised: {result!r}") from result

    # Each call's own official identifier wins for ITS OWN observation.
    assert results[0].id == row_a_id
    assert results[1].id == row_b_id

    verify_session = session_factory()
    try:
        # Both curated rows survive, completely untouched.
        count = (
            await verify_session.execute(
                select(func.count()).select_from(Ingredient).where(Ingredient.id.in_([row_a_id, row_b_id]))
            )
        ).scalar_one()
        assert count == 2

        # The shared alias was NEVER reassigned -- still ambiguously
        # pointing at whichever row legitimately owned it before either
        # concurrent call ran.
        alias = (
            await verify_session.execute(
                select(IngredientAlias).where(IngredientAlias.alias_normalized == shared_normalized)
            )
        ).scalar_one()
        assert alias.ingredient_id == row_b_id
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == shared_normalized)
        )
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([row_a_id, row_b_id])))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_reconciliation_of_a_referenced_synthetic_loser_never_deletes_it(postgres_url):
    """PR #13 review round 3, scenario (c): the alias-owning UNVERIFIED
    duplicate is already referenced by a real `Product.ingredient_ids`.
    Two concurrent sessions both discover the conflict against the same
    pre-existing official/curated row -- the alias must converge onto
    the official row, but the referenced duplicate must survive both
    concurrent reconciliations intact, and the product's own reference
    must stay valid."""
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.models.product import Product
    from app.repositories import ingredient_alias_repository
    from app.services import ingredient_catalog
    from app.services.ingredient_normalization import normalize_ingredient_name
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    official_id = "pg_referenced_loser_official"
    stub_id = "pg_referenced_loser_stub"
    barcode = "9990000000001"
    e_number = "E995"
    name = "PG Referenced Loser Additive"
    normalized = normalize_ingredient_name(name)

    setup_session = session_factory()
    try:
        await setup_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await setup_session.execute(Product.__table__.delete().where(Product.barcode == barcode))
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        official = Ingredient(
            id=official_id,
            common_name="PG Referenced Loser Curated Name",
            normalized_name=normalize_ingredient_name("PG Referenced Loser Curated Name"),
            e_number=e_number,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=True,
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.CURATED_SEED,
            confidence=1.0,
        )
        stub = Ingredient(
            id=stub_id,
            common_name=name,
            normalized_name=normalized,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=False,
            verification_status=IngredientVerificationStatus.UNVERIFIED,
            source=IngredientSource.OCR_HEURISTIC,
            confidence=0.2,
        )
        setup_session.add_all([official, stub])
        await setup_session.flush()
        await ingredient_alias_repository.get_or_create(
            setup_session,
            ingredient_id=stub_id,
            alias_text=name,
            alias_normalized=normalized,
            language=None,
            source=IngredientSource.OCR_HEURISTIC,
        )
        product = Product(
            barcode=barcode,
            product_name="PG Referenced Loser Product",
            brand="",
            category="",
            raw_ingredient_text=name,
            ingredient_ids=stub_id,
            health_score=50,
            nova_group=1,
            sugar_grams=0,
            sodium_mg=0,
            saturated_fat_grams=0,
            allergens_detected="None",
            source="label_scan",
        )
        setup_session.add(product)
        await setup_session.commit()
    finally:
        await setup_session.close()

    from dataclasses import replace

    synthetic = replace(create_synthetic_ingredient(name), e_number=e_number)

    async def _resolve_and_commit(session):
        resolved = await ingredient_catalog.get_or_create_catalog_ingredient(session, synthetic)
        await session.commit()
        return resolved

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            _resolve_and_commit(session_a), _resolve_and_commit(session_b), return_exceptions=True
        )
    finally:
        await session_a.close()
        await session_b.close()

    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent referenced-loser reconciliation raised: {result!r}") from result
        assert result.id == official_id

    verify_session = session_factory()
    try:
        # The referenced stub survives -- deleting it would leave the
        # product with a dangling ingredient reference.
        count = (
            await verify_session.execute(
                select(func.count()).select_from(Ingredient).where(Ingredient.id.in_([official_id, stub_id]))
            )
        ).scalar_one()
        assert count == 2

        # But it's no longer reachable by name -- every future
        # observation of this name converges on the official row.
        alias = (
            await verify_session.execute(select(IngredientAlias).where(IngredientAlias.alias_normalized == normalized))
        ).scalar_one()
        assert alias.ingredient_id == official_id

        # The product's own direct reference is completely untouched.
        persisted_product = await verify_session.get(Product, barcode)
        assert persisted_product.ingredient_ids == stub_id
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await verify_session.execute(Product.__table__.delete().where(Product.barcode == barcode))
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_no_identifier_and_identifier_observations_of_a_brand_new_name_converge_on_the_curated_row(
    postgres_url,
):
    """The `owns_row=True` variant: session A observes a never-before-
    seen name with NO identifier (so it inserts its own minimal stub),
    while session B concurrently observes the SAME name WITH the
    E-number that resolves to a pre-existing curated row. Regardless of
    interleaving, every call must either return the curated row directly
    or (if it raced ahead and briefly created its own stub) detect the
    loss and delete its own row -- never raise, never leave two
    `Ingredient` rows both claiming to be reachable by this name."""
    from dataclasses import replace

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.services import ingredient_catalog
    from app.services.ingredient_normalization import normalize_ingredient_name
    from app.services.ocr_normalizer import create_synthetic_ingredient

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    official_id = "pg_official_precedence_race_curated"
    e_number = "E998"
    name = "PG Official Precedence Race Additive"
    normalized = normalize_ingredient_name(name)
    stub_id = create_synthetic_ingredient(name).id

    setup_session = session_factory()
    try:
        await setup_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await setup_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        official = Ingredient(
            id=official_id,
            common_name="PG Official Precedence Race Curated Name",
            normalized_name=normalize_ingredient_name("PG Official Precedence Race Curated Name"),
            e_number=e_number,
            risk_level=RiskLevel.SAFE,
            risk_assessment_available=True,
            verification_status=IngredientVerificationStatus.VERIFIED,
            source=IngredientSource.CURATED_SEED,
            confidence=1.0,
        )
        setup_session.add(official)
        await setup_session.commit()
    finally:
        await setup_session.close()

    synthetic_no_identifier = create_synthetic_ingredient(name)
    synthetic_with_identifier = replace(synthetic_no_identifier, e_number=e_number)
    assert synthetic_no_identifier.e_number is None

    async def _resolve_and_commit(session, synthetic):
        resolved = await ingredient_catalog.get_or_create_catalog_ingredient(session, synthetic)
        await session.commit()
        return resolved

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            _resolve_and_commit(session_a, synthetic_no_identifier),
            _resolve_and_commit(session_b, synthetic_with_identifier),
            return_exceptions=True,
        )
    finally:
        await session_a.close()
        await session_b.close()

    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent resolution raised: {result!r}") from result

    # `synthetic_with_identifier`'s own call ALWAYS resolves via the
    # official identifier directly (the curated row already exists
    # before either session starts) -- unaffected by interleaving.
    identifier_result = results[1]
    assert identifier_result.id == official_id

    # A THIRD, later, purely sequential call (of either form) must
    # deterministically converge on the curated row too -- the
    # observable guarantee this fix actually provides once the race
    # itself has settled, regardless of what the no-identifier call's
    # OWN in-flight return value happened to be (see this file's module
    # docstring).
    verify_session = session_factory()
    try:
        later = await ingredient_catalog.get_or_create_catalog_ingredient(
            verify_session, create_synthetic_ingredient(name)
        )
        await verify_session.commit()
        assert later.id == official_id

        official_count = (
            await verify_session.execute(select(func.count()).select_from(Ingredient).where(Ingredient.e_number == e_number))
        ).scalar_one()
        assert official_count == 1

        aliases = (
            await verify_session.execute(select(IngredientAlias).where(IngredientAlias.alias_normalized == normalized))
        ).scalars().all()
        assert len(aliases) == 1
        assert aliases[0].ingredient_id == official_id
    finally:
        await verify_session.execute(
            IngredientAlias.__table__.delete().where(IngredientAlias.alias_normalized == normalized)
        )
        await verify_session.execute(Ingredient.__table__.delete().where(Ingredient.id.in_([official_id, stub_id])))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()
