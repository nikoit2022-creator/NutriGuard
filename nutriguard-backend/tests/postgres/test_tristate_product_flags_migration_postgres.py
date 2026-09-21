"""
Real PostgreSQL test for migration d7e8f9a0b1c2 (tri-state product dietary
flags, nullable Health Score, honest allergens): upgrade -> downgrade ->
upgrade with representative pre-existing products / ingredients / aliases /
scan history / product-source links, and a single Alembic head.

Deliberately NOT part of the default `pytest -q` run -- same opt-in
convention as the sibling files here: set `NUTRIGUARD_TEST_POSTGRES_URL`
to a real, DISPOSABLE PostgreSQL instance (server-level access to a
throwaway server; e.g. `postgresql+asyncpg://user:pw@host:5432/postgres`).
This test never touches the tables of that database: it creates its OWN
fresh, uniquely named database on the same server, runs the real Alembic
migrations there in a subprocess (`DATABASE_URL` is what `alembic/env.py`
reads), and drops it afterwards.

What it proves that the SQLite policy test cannot: the real DDL
(`ALTER COLUMN ... DROP NOT NULL` / `SET NOT NULL`), the data statements
and the Python flag policy under PostgreSQL semantics (native enum columns,
real booleans), that the downgrade backfill lets NOT NULL be re-applied,
and that unrelated tables/rows and product <-> ingredient / source / history
links are untouched.

PR #22 review finding 1: representative legacy rows include a `false` next
to "gluten-free", "free from milk", plant-milk text, a genuine ingredient
beside a negated phrase, a declared dairy allergen, and a trusted vs an
untrusted linked catalog ingredient -- the actual policy must keep only the
supported ones.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NUTRIGUARD_TEST_POSTGRES_URL"),
    reason="Opt-in: set NUTRIGUARD_TEST_POSTGRES_URL to a real, disposable Postgres server to run this test.",
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PRE_REVISION = "c6d7e8f9a0b1"
HEAD_REVISION = "d7e8f9a0b1c2"
FLAGS = ("is_gluten_free", "is_lactose_free", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")


def _alembic(database_url: str, *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(BACKEND_ROOT / "alembic.ini"), *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout + result.stderr


@pytest.fixture
def fresh_database():
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    server_url = make_url(os.environ["NUTRIGUARD_TEST_POSTGRES_URL"])
    name = f"ng_migration_{uuid.uuid4().hex[:12]}"
    admin = create_engine(server_url.set(drivername="postgresql+psycopg2"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    async_url = server_url.set(drivername="postgresql+asyncpg", database=name).render_as_string(hide_password=False)
    sync_url = server_url.set(drivername="postgresql+psycopg2", database=name)
    try:
        yield async_url, sync_url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _seed_pre_migration_rows(sync_url) -> None:
    """Representative legacy data at the PRE-migration schema (every flag /
    score NOT NULL, allergen placeholder 'None')."""
    import json

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.auth import User
    from app.models.enums import IngredientSource, IngredientVerificationStatus, ScanType
    from app.models.ingredient import Ingredient
    from app.models.ingredient_alias import IngredientAlias
    from app.models.product import Product
    from app.models.product_source import ProductSource
    from app.models.scan_history import ScanHistory
    from app.seed import load_seed as load_seed_module

    engine = create_engine(sync_url)
    all_true = {flag: True for flag in FLAGS}

    linked_ids = {"off-verified": "e951_aspartame", "catalog-supported": "test_trusted_gluten",
                  "catalog-untrusted": "test_untrusted_gluten", "catalog-gemini": "test_gemini_gluten",
                  "catalog-substring": "test_milk_row", "catalog-milk-real": "test_milk_row",
                  "qualified-catalog": "test_milk_row"}

    def product(barcode, source, text, verified, score, allergens="None", **flags):
        values = {flag: False for flag in FLAGS}
        values.update(flags)
        return Product(
            barcode=barcode, product_name=f"P {barcode}", brand="B", category="C", raw_ingredient_text=text,
            ingredient_ids=linked_ids.get(barcode, ""), health_score=score, nova_group=3,
            allergens_detected=allergens, source=source, is_verified=verified, has_verified_nutrition=verified,
            has_verified_ingredients=verified, **values,
        )

    seed_rows = json.loads(load_seed_module._SEED_FILE.read_text(encoding="utf-8"))
    seed_row = next(r for r in seed_rows if r["id"] == "e951_aspartame")

    def catalog_ingredient(id_, name, **overrides):
        kwargs = load_seed_module._row_to_kwargs(seed_row)
        kwargs.update(id=id_, common_name=name, normalized_name=name.lower(), e_number=None, ins_number=None, **overrides)
        return Ingredient(**kwargs)

    with Session(engine) as session:
        user = User()
        session.add(user)
        session.add(Ingredient(**load_seed_module._row_to_kwargs(seed_row)))
        session.add(catalog_ingredient("test_trusted_gluten", "Barley Malt Extract", is_gluten=True))  # VERIFIED + CURATED_SEED
        session.add(catalog_ingredient(
            "test_untrusted_gluten", "Rye Sour Extract", is_gluten=True,
            verification_status=IngredientVerificationStatus.UNVERIFIED, source=IngredientSource.OCR_HEURISTIC,
        ))
        session.add(catalog_ingredient(  # VERIFIED status but an untrusted source: not evidence either
            "test_gemini_gluten", "Spelt Extract", is_gluten=True, source=IngredientSource.GEMINI,
        ))
        session.add(catalog_ingredient("test_milk_row", "Milk", is_lactose=True, is_vegan=False))  # trusted, generic name
        session.flush()
        session.add(IngredientAlias(ingredient_id="e951_aspartame", alias_text="Aspartame", alias_normalized="aspartame",
                                    language="en", source=IngredientSource.CURATED_SEED))
        session.add_all([
            product("off-verified", "open_food_facts", "Wheat flour, aspartame", True, 55,
                    is_vegan=True, is_gluten_free=False),
            product("bg-ocr", "label_scan", "Пшенично брашно, мляко, сол", False, 0, **all_true),
            product("verified-zero", "label_scan", "pork gelatin", True, 0, allergens="Soy",
                    is_halal=False, is_kosher=False, is_vegetarian=False, is_vegan=False),
            product("local", "local", "water", True, 80, **all_true),
            # --- PR #22 review: legacy `false` defaults next to negated / ambiguous / genuine text -------------
            product("gluten-free-label", "label_scan", "Gluten-free oat flour", False, 0),
            product("free-from-milk", "label_scan", "Free from milk, without wheat", False, 0),
            product("plant-milk", "label_scan", "Coconut milk, water", False, 0, allergens="Milk"),
            product("genuine-plus-negated", "label_scan", "Gluten-free oats, wheat flour. Free from milk.", False, 0),
            product("dairy-declared", "label_scan", "Contains: Milk, Soy", False, 0, allergens="Soy, Milk"),
            product("catalog-supported", "label_scan", "Пшенично брашно, Barley Malt Extract", False, 0),
            product("catalog-untrusted", "label_scan", "Пшенично брашно, Rye Sour Extract", False, 0),
            product("catalog-gemini", "label_scan", "Spelt Extract, water", False, 0),
            product("catalog-substring", "label_scan", "Coconut milk, water", False, 0),  # linked to trusted "Milk" by substring
            product("catalog-milk-real", "label_scan", "Water, Milk", False, 0),
            product("wrapped-negated", "label_scan", "Gluten-\nfree oat flour. May contain\nmilk, soy", False, 0),
            # --- PR #22 owner follow-up: a qualifier stays attached to its parent -----------------------------
            product("qualified-plant", "label_scan", "Ingredients: sugar, milk (plant-based), salt", False, 0),
            product("qualified-provider-true", "open_food_facts", "Milk (coconut)", False, 0, is_vegan=True),
            product("qualified-catalog", "label_scan", "Milk (plant-based), water", False, 0),  # trusted "Milk" row linked
            product("qualified-quantity", "label_scan", "Milk (3%), sugar", False, 0),
            product("qualified-sublist", "label_scan", "Chocolate (sugar, whole milk powder)", False, 0),
        ])
        session.flush()
        session.add(ProductSource(barcode="off-verified", provider="open_food_facts", confidence=0.75))
        session.add(ScanHistory(user_id=user.id, barcode="off-verified", product_name="P off-verified", brand="B",
                                health_score=55, scan_type=ScanType.BARCODE))
        session.commit()
    engine.dispose()


def _products(sync_url) -> dict:
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM products")).mappings().all()
    engine.dispose()
    return {row["barcode"]: dict(row) for row in rows}


def _scalar(sync_url, sql: str):
    from sqlalchemy import create_engine, text

    engine = create_engine(sync_url)
    with engine.connect() as conn:
        value = conn.execute(text(sql)).scalar()
    engine.dispose()
    return value


def _is_nullable(sync_url, column: str) -> bool:
    return _scalar(
        sync_url,
        "SELECT is_nullable FROM information_schema.columns "
        f"WHERE table_name = 'products' AND column_name = '{column}'",
    ) == "YES"


def _known(row: dict) -> dict:
    return {flag: row[flag] for flag in FLAGS if row[flag] is not None}


def _assert_review_policy(rows: dict) -> None:
    """The legacy-data policy's outcome for the PR #22 review rows (every row
    started with all six flags `false`, the old NOT NULL default)."""
    assert _known(rows["gluten-free-label"]) == {}  # "gluten" only inside "gluten-free"
    assert _known(rows["free-from-milk"]) == {}  # negated: "free from milk", "without wheat"
    assert _known(rows["plant-milk"]) == {}  # "coconut milk" is not a dairy entry
    assert rows["plant-milk"]["allergens_detected"] == "Milk"  # positive allergens are not rewritten (documented)
    # a genuine "wheat flour" next to "gluten-free oats" and a negated phrase is NOT discarded globally
    assert _known(rows["genuine-plus-negated"]) == {"is_gluten_free": False}
    assert _known(rows["dairy-declared"]) == {"is_vegan": False}  # dairy declaration kept; lactose not inferred from milk
    assert rows["dairy-declared"]["allergens_detected"] == "Soy, Milk"
    assert _known(rows["catalog-supported"]) == {"is_gluten_free": False}  # linked TRUSTED catalog row
    assert _known(rows["catalog-untrusted"]) == {}  # linked UNVERIFIED OCR row: a bool is not evidence
    assert _known(rows["catalog-gemini"]) == {}  # VERIFIED status but a GEMINI source: not evidence
    assert _known(rows["catalog-substring"]) == {}  # "coconut milk" reaches the trusted "Milk" row only by substring
    assert _known(rows["catalog-milk-real"]) == {"is_lactose_free": False, "is_vegan": False}  # an exact "Milk" entry
    assert _known(rows["wrapped-negated"]) == {}  # line-wrapped "gluten-free" / "may contain" are not ingredients
    # PR #22 owner follow-up: "milk (plant-based)" is not a dairy entry -- not from the text, not via a trusted "Milk" row
    assert _known(rows["qualified-plant"]) == {}
    assert _known(rows["qualified-provider-true"]) == {"is_vegan": True}  # explicit provider claim not erased by it
    assert _known(rows["qualified-catalog"]) == {}
    assert _known(rows["qualified-quantity"]) == {"is_vegan": False}  # an ordinary quantity qualifier keeps the identity
    assert _known(rows["qualified-sublist"]) == {"is_vegan": False}  # a genuine compound sublist keeps its evidence


def _assert_links_and_text_preserved(sync_url, pre: dict) -> None:
    """Data/link preservation: product text and ingredient/source/history links
    are byte-for-byte what they were before the policy ran."""
    now = _products(sync_url)
    for barcode, before in pre.items():
        for column in ("raw_ingredient_text", "ingredient_ids", "source", "product_name", "is_verified"):
            assert now[barcode][column] == before[column], (barcode, column)
    assert _scalar(sync_url, "SELECT COUNT(*) FROM product_sources WHERE barcode = 'off-verified'") == 1
    assert _scalar(sync_url, "SELECT COUNT(*) FROM scan_history WHERE barcode = 'off-verified'") == 1
    assert _scalar(sync_url, "SELECT COUNT(*) FROM ingredient_aliases WHERE ingredient_id = 'e951_aspartame'") == 1
    assert _scalar(sync_url, "SELECT is_gluten FROM ingredients WHERE id = 'test_untrusted_gluten'") is True  # catalog rows untouched


def test_single_alembic_head_on_real_postgres(fresh_database):
    async_url, _ = fresh_database
    heads = _alembic(async_url, "heads")
    assert heads.count("(head)") == 1
    assert HEAD_REVISION in heads


def test_upgrade_downgrade_upgrade_round_trip_with_representative_data(fresh_database):
    async_url, sync_url = fresh_database

    # --- pre-migration schema + representative legacy data -----------------
    _alembic(async_url, "upgrade", PRE_REVISION)
    assert not _is_nullable(sync_url, "health_score")
    assert all(not _is_nullable(sync_url, flag) for flag in FLAGS)
    _seed_pre_migration_rows(sync_url)
    other_tables = ("ingredients", "ingredient_aliases", "scan_history", "product_sources", "users")
    counts_before = {table: _scalar(sync_url, f"SELECT COUNT(*) FROM {table}") for table in other_tables}
    assert all(count >= 1 for count in counts_before.values()), counts_before
    assert _scalar(sync_url, "SELECT COUNT(*) FROM products") == 20
    pre = _products(sync_url)

    # --- upgrade -----------------------------------------------------------
    _alembic(async_url, "upgrade", "head")
    assert _is_nullable(sync_url, "health_score")
    assert all(_is_nullable(sync_url, flag) for flag in FLAGS)
    up = _products(sync_url)
    assert up["off-verified"]["is_vegan"] is True  # provider True kept
    assert up["off-verified"]["is_gluten_free"] is False  # keyword ("wheat") still supports the False
    assert up["off-verified"]["is_lactose_free"] is None  # default False, no evidence -> unknown
    assert up["off-verified"]["health_score"] == 55
    assert all(up["bg-ocr"][flag] is None for flag in FLAGS)  # keyword-absence Trues invalidated
    assert up["bg-ocr"]["health_score"] is None  # unverified placeholder 0 -> NULL
    assert up["bg-ocr"]["allergens_detected"] == ""  # "None" placeholder -> ""
    assert up["verified-zero"]["health_score"] == 0  # genuine 0 on a verified row preserved
    assert up["verified-zero"]["is_halal"] is False and up["verified-zero"]["allergens_detected"] == "Soy"
    assert all(up["local"][flag] is None for flag in FLAGS) and up["local"]["health_score"] == 80
    _assert_review_policy(up)
    _assert_links_and_text_preserved(sync_url, pre)
    for table, before in counts_before.items():  # unrelated data untouched
        assert _scalar(sync_url, f"SELECT COUNT(*) FROM {table}") == before, table

    # --- a row written AFTER the upgrade with genuinely unknown values ------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.models.product import Product

    engine = create_engine(sync_url)
    with Session(engine) as session:
        session.add(Product(barcode="post-upgrade", product_name="N", raw_ingredient_text="", ingredient_ids="", nova_group=3))
        session.commit()
    engine.dispose()
    post = _products(sync_url)["post-upgrade"]
    assert all(post[flag] is None for flag in FLAGS) and post["health_score"] is None and post["allergens_detected"] == ""

    # --- downgrade (documented lossy backfill) ------------------------------
    _alembic(async_url, "downgrade", PRE_REVISION)
    assert not _is_nullable(sync_url, "health_score")
    assert all(not _is_nullable(sync_url, flag) for flag in FLAGS)
    down = _products(sync_url)
    assert all(down["bg-ocr"][flag] is False for flag in FLAGS)  # unknown -> false (lossy, documented)
    assert down["bg-ocr"]["health_score"] == 0  # unknown -> old placeholder
    assert down["post-upgrade"]["health_score"] == 0
    assert down["off-verified"]["is_vegan"] is True  # kept values untouched
    assert down["verified-zero"]["health_score"] == 0
    assert down["bg-ocr"]["allergens_detected"] == ""  # "None" is NOT restored (documented)
    _assert_links_and_text_preserved(sync_url, pre)
    for table, before in counts_before.items():
        assert _scalar(sync_url, f"SELECT COUNT(*) FROM {table}") == before, table

    # --- upgrade again ------------------------------------------------------
    _alembic(async_url, "upgrade", "head")
    again = _products(sync_url)
    assert all(_is_nullable(sync_url, flag) for flag in FLAGS) and _is_nullable(sync_url, "health_score")
    assert again["off-verified"]["is_vegan"] is True  # provider evidence survives the whole cycle
    assert again["off-verified"]["is_gluten_free"] is False
    assert again["verified-zero"]["health_score"] == 0  # verified genuine 0 survives the whole cycle
    assert again["bg-ocr"]["health_score"] is None  # unverified 0 backfill is unknown again
    # The lossy backfill (unknown -> false) is re-evaluated by the same policy: no evidence -> unknown
    assert all(again["bg-ocr"][flag] is None for flag in FLAGS)
    assert again["post-upgrade"]["health_score"] is None
    _assert_review_policy(again)  # the actual policy, re-run on the round-tripped (backfilled) data
    _assert_links_and_text_preserved(sync_url, pre)
    assert _scalar(sync_url, "SELECT COUNT(*) FROM products") == 21
