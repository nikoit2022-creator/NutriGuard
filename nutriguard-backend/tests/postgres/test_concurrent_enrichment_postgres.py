"""
Real concurrent-session PostgreSQL test for the barcode-enrichment
INSERT race (review finding 3: "add a real concurrent-session
PostgreSQL test, or clearly document and test the strongest guarantee
actually provided").

Deliberately NOT part of the default `pytest -q` run (which is
SQLite-backed and has no Postgres available -- see README section 3):
this file is opt-in, exactly like the rest of this project's existing
convention that real DDL/transaction behavior against Postgres is a
manual/CI concern, not something the fast SQLite suite attempts (see
README "10.7 Running the mocked tests / verifying the migration" for
the same pattern applied to the barcode-discovery migration).

How to run:

    docker run --rm -d --name <some-throwaway-name> \\
        -e POSTGRES_PASSWORD=test -e POSTGRES_DB=nutriguard_test \\
        -p 55432:5432 postgres:16-alpine
    alembic upgrade head   # with DATABASE_URL pointing at it
    NUTRIGUARD_TEST_POSTGRES_URL="postgresql+asyncpg://postgres:test@localhost:55432/nutriguard_test" \\
        pytest tests/postgres/test_concurrent_enrichment_postgres.py -v
    docker stop <some-throwaway-name>

Every test here uses its OWN separate `AsyncSession` bound to a real
connection pool against real Postgres -- i.e. genuinely separate
database connections/transactions, not the single shared SQLite
connection the rest of the suite uses (see `tests/conftest.py`'s
`db_engine`, and `product_repository.insert_new`'s own docstring on why
that fixture cannot faithfully exercise a real race).

This file proves exactly what `food_analysis._persist_enriched_product`
claims and no more: the brand-new-row INSERT race (via
`product_repository.insert_new`'s SAVEPOINT-based conflict handling) is
genuinely safe under concurrent transactions. It deliberately does NOT
claim the EXISTING-row merge path is linearizable -- see that
function's docstring for the honest statement of what is only best
effort there.
"""
import asyncio
import io
import json
import os
import uuid

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres instance to run this test.",
)


@pytest.fixture(scope="module")
def postgres_url() -> str:
    return os.environ["NUTRIGUARD_TEST_POSTGRES_URL"]


def _fake_jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color=(10, 20, 30)).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_concurrent_enrichment_of_the_same_new_barcode_converges_on_one_row(postgres_url, monkeypatch):
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.integrations.gemini import gemini_service
    from app.models.auth import User
    from app.models.product import Product
    from app.models.product_source import ProductSource
    from app.services import food_analysis

    engine = create_async_engine(postgres_url, future=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    # A real, checksum-valid EAN-13 not used by any other test in this file.
    barcode = "4006381333931"
    image_bytes = _fake_jpeg_bytes()

    gemini_payload = {
        "productName": "Concurrent Test Product",
        "brand": "Concurrent Test Brand",
        "sugarGrams": 5.0,
        "sodiumMg": 50.0,
        "saturatedFatGrams": 1.0,
        "hasArtificialSweeteners": False,
        "hasPreservatives": False,
        "isGlutenFree": True,
        "isLactoseFree": True,
        "isVegan": True,
        "isVegetarian": True,
        "isHalal": True,
        "isKosher": True,
        "novaGroup": 2,
        "rawIngredientText": "Water, Sugar, Salt",
        "ingredients": [{"commonName": "Sugar"}, {"commonName": "Salt"}],
    }

    async def fake_analyze_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        return json.dumps(gemini_payload)

    # `gemini_service` is a module-level singleton -- patching it here
    # affects both concurrent calls, exactly like patching it affects
    # both concurrent requests in a real multi-worker deployment.
    monkeypatch.setattr(gemini_service, "analyze_image", fake_analyze_image)

    setup_session = session_factory()
    try:
        user_a = User()
        user_b = User()
        setup_session.add_all([user_a, user_b])
        await setup_session.commit()
        user_a_id, user_b_id = user_a.id, user_b.id

        # Clean slate: this barcode must not already exist from a
        # previous run against the same disposable database.
        await setup_session.execute(
            ProductSource.__table__.delete().where(ProductSource.barcode == barcode)
        )
        await setup_session.execute(Product.__table__.delete().where(Product.barcode == barcode))
        await setup_session.commit()
    finally:
        await setup_session.close()

    session_a = session_factory()
    session_b = session_factory()
    try:
        results = await asyncio.gather(
            food_analysis.analyze_label_image_with_barcode(session_a, user_a_id, image_bytes, barcode),
            food_analysis.analyze_label_image_with_barcode(session_b, user_b_id, image_bytes, barcode),
            return_exceptions=True,
        )
    finally:
        await session_a.close()
        await session_b.close()

    # Neither concurrent call may crash or raise -- both a genuine
    # winner (inserts) and a genuine loser (loses the SAVEPOINT
    # conflict, re-fetches, and uses the now-committed row) must
    # complete successfully.
    for result in results:
        if isinstance(result, BaseException):
            raise AssertionError(f"concurrent enrichment call raised: {result!r}") from result
        assert result["product"].barcode == barcode

    verify_session = session_factory()
    try:
        count = (
            await verify_session.execute(
                select(func.count()).select_from(Product).where(Product.barcode == barcode)
            )
        ).scalar_one()
        assert count == 1, f"expected exactly one Product row for {barcode}, found {count}"

        product = (
            await verify_session.execute(select(Product).where(Product.barcode == barcode))
        ).scalar_one()
        assert product.product_name == "Concurrent Test Product"
        assert product.has_verified_nutrition is True

        # Both concurrent calls' OCR provenance attempts are recorded
        # (insert-or-refresh by (barcode, provider) -- see
        # `product_source_repository.record_discovery`), never a
        # uniqueness crash from the second one landing after the first.
        source_count = (
            await verify_session.execute(
                select(func.count()).select_from(ProductSource).where(ProductSource.barcode == barcode)
            )
        ).scalar_one()
        assert source_count == 1  # one (barcode, "label_ocr") row, refreshed in place, not duplicated
    finally:
        # Leave the disposable database clean for a re-run.
        await verify_session.execute(
            ProductSource.__table__.delete().where(ProductSource.barcode == barcode)
        )
        await verify_session.execute(Product.__table__.delete().where(Product.barcode == barcode))
        await verify_session.execute(User.__table__.delete().where(User.id.in_([user_a_id, user_b_id])))
        await verify_session.commit()
        await verify_session.close()

    await engine.dispose()
