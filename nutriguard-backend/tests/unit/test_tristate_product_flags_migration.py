"""
Migration d7e8f9a0b1c2 (tri-state product dietary flags, nullable Health
Score, honest allergens): chain/shape checks PLUS an executable check of
its legacy-data policy -- the migration exposes its data statements as
plain SQL (`data_policy_statements` / `downgrade_backfill_statements`) and
this test runs exactly those statements against representative legacy
rows in SQLite. (The DDL half -- ALTER COLUMN, upgrade -> downgrade ->
upgrade -- needs a real PostgreSQL: see
`tests/postgres/test_tristate_product_flags_migration_postgres.py`.)
"""
import importlib.util
import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "d7e8f9a0b1c2"
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "d7e8f9a0b1c2_tristate_product_flags_nullable_score.py"

FLAGS = ("is_gluten_free", "is_lactose_free", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_d7e8f9a0b1c2", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_is_the_single_head_and_chains_from_the_previous_head():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == "c6d7e8f9a0b1"
    assert scripts.get_heads() == [REVISION]


def test_migration_covers_exactly_the_intended_columns_and_backfills_before_not_null():
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    for column in (*FLAGS, "health_score"):
        assert f'"{column}"' in source
    assert "nullable=True" in source and "nullable=False" in source
    downgrade_body = source.split("def downgrade()")[1]
    # every backfill statement runs before the first NOT NULL is re-added
    assert downgrade_body.index("downgrade_backfill_statements") < downgrade_body.index("nullable=False")


def _legacy_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    columns = ", ".join(f"{flag} BOOLEAN" for flag in FLAGS)
    db.execute(
        "CREATE TABLE products (barcode TEXT PRIMARY KEY, source TEXT NOT NULL, raw_ingredient_text TEXT NOT NULL, "
        f"is_verified BOOLEAN NOT NULL, health_score INTEGER, allergens_detected TEXT NOT NULL, {columns})"
    )
    return db


def _insert(db, barcode, *, source, text, verified, score, allergens="None", **flags):
    values = {flag: flags.get(flag) for flag in FLAGS}
    db.execute(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (barcode, source, text, verified, score, allergens, *(values[f] for f in FLAGS)),
    )


def _row(db, barcode) -> dict:
    cursor = db.execute("SELECT * FROM products WHERE barcode = ?", (barcode,))
    names = [d[0] for d in cursor.description]
    return dict(zip(names, cursor.fetchone()))


def _representative_legacy_rows() -> sqlite3.Connection:
    db = _legacy_db()
    all_true = {flag: True for flag in FLAGS}
    all_false = {flag: False for flag in FLAGS}
    # 1. provider row: explicit tags (vegan True), defaults (rest False), text names wheat; verified, real score
    _insert(db, "off", source="open_food_facts", text="Wheat flour, sugar", verified=True, score=55,
            is_vegan=True, is_gluten_free=False, is_lactose_free=False, is_vegetarian=False, is_halal=False, is_kosher=False)
    # 2. OCR-heuristic row on Bulgarian text: every flag a keyword-absence guess (True), unverified placeholder 0
    _insert(db, "bg-ocr", source="label_scan", text="Пшенично брашно, мляко, сол", verified=False, score=0, **all_true)
    # 3. label row with an English keyword hit for vegan/lactose + an explicit-default gluten False
    _insert(db, "en-label", source="label_scan_translated", text="MILK powder, sugar", verified=False, score=0,
            is_vegan=False, is_lactose_free=False, is_gluten_free=False, is_vegetarian=True, is_halal=True, is_kosher=True)
    # 4. verified label row with a genuine computed 0 and pork evidence
    _insert(db, "verified-zero", source="label_scan", text="pork gelatin", verified=True, score=0, allergens="Soy",
            is_halal=False, is_kosher=False, is_vegetarian=False, is_vegan=False, is_gluten_free=True, is_lactose_free=True)
    # 5. legacy 'local' row and an unrecognised source: unsupported Trues
    _insert(db, "local", source="local", text="water", verified=True, score=80, **all_true)
    _insert(db, "weird", source="some_future_source", text="water", verified=False, score=0, **all_true)
    # 6. provider row with everything False by default and no keyword evidence at all
    _insert(db, "off-defaults", source="upcitemdb", text="water, salt", verified=False, score=0, **all_false)
    # 7. allergen placeholder variants
    for i, allergens in enumerate([" none ", "N/A", "Null", "Milk, Soy", "", "-"]):
        _insert(db, f"alg{i}", source="local", text="water", verified=True, score=10, allergens=allergens)
    return db


def test_legacy_data_policy_keeps_evidence_and_resets_guesses():
    db = _representative_legacy_rows()
    for statement in _load_migration().data_policy_statements():
        db.execute(statement)

    off = _row(db, "off")
    assert off["is_vegan"] == 1  # provider's explicit True kept
    assert off["is_gluten_free"] == 0  # False kept: "wheat" is still in the stored text
    assert off["is_lactose_free"] is None  # provider default False, no milk keyword -> unknown
    assert off["is_halal"] is None and off["is_kosher"] is None and off["is_vegetarian"] is None
    assert off["health_score"] == 55  # verified score untouched
    assert off["allergens_detected"] == ""

    bg = _row(db, "bg-ocr")
    assert all(bg[flag] is None for flag in FLAGS)  # every keyword-absence "True" guess invalidated
    assert bg["health_score"] is None  # unverified placeholder 0 -> unknown

    en = _row(db, "en-label")
    assert en["is_vegan"] == 0 and en["is_lactose_free"] == 0  # keyword-supported Falses kept ("milk")
    assert en["is_gluten_free"] is None  # no gluten/wheat text -> unsupported False reset
    assert en["is_vegetarian"] is None and en["is_halal"] is None and en["is_kosher"] is None  # non-provider Trues

    zero = _row(db, "verified-zero")
    assert zero["health_score"] == 0  # a GENUINE 0 on a verified row survives
    assert (zero["is_halal"], zero["is_kosher"], zero["is_vegetarian"], zero["is_vegan"]) == (0, 0, 0, 0)  # "pork"
    assert zero["is_gluten_free"] is None and zero["is_lactose_free"] is None  # non-provider Trues reset
    assert zero["allergens_detected"] == "Soy"  # known positive allergen evidence preserved

    for barcode in ("local", "weird"):
        assert all(_row(db, barcode)[flag] is None for flag in FLAGS), barcode
    assert _row(db, "local")["health_score"] == 80  # verified 'local' score kept
    assert _row(db, "weird")["health_score"] is None

    assert all(_row(db, "off-defaults")[flag] is None for flag in FLAGS)  # all-False defaults -> unknown

    assert [_row(db, f"alg{i}")["allergens_detected"] for i in range(6)] == ["", "", "", "Milk, Soy", "", ""]


def test_legacy_data_policy_is_idempotent():
    db = _representative_legacy_rows()
    statements = _load_migration().data_policy_statements()
    for statement in statements:
        db.execute(statement)
    once = db.execute("SELECT * FROM products ORDER BY barcode").fetchall()
    for statement in statements:
        db.execute(statement)
    assert db.execute("SELECT * FROM products ORDER BY barcode").fetchall() == once


def test_downgrade_backfill_leaves_no_nulls_so_not_null_can_be_restored():
    db = _representative_legacy_rows()
    migration = _load_migration()
    for statement in migration.data_policy_statements():
        db.execute(statement)
    assert db.execute("SELECT COUNT(*) FROM products WHERE health_score IS NULL").fetchone()[0] > 0
    for statement in migration.downgrade_backfill_statements():
        db.execute(statement)
    for column in (*FLAGS, "health_score"):
        assert db.execute(f"SELECT COUNT(*) FROM products WHERE {column} IS NULL").fetchone()[0] == 0, column
    # backfill values: unknown flag -> false, unscored -> the old 0 placeholder; allergens stay ""
    bg = _row(db, "bg-ocr")
    assert all(bg[flag] == 0 for flag in FLAGS) and bg["health_score"] == 0
    assert _row(db, "off")["is_vegan"] == 1  # a kept provider True is not touched by the backfill
    assert _row(db, "alg0")["allergens_detected"] == ""


def test_migration_never_imports_application_code():
    """Frozen SQL copies of the keyword/placeholder sets, by design: a migration
    must not change meaning when application code later evolves."""
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source
