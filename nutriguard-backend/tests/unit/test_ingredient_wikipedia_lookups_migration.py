"""Migration b9c0d1e2f3a4: one new `ingredient_source` enum value
(WIKIPEDIA_API) plus one new, purely additive table
(`ingredient_wikipedia_lookups`) on top of a8b9c0d1e2f3 (real upgrade/
downgrade on PostgreSQL is exercised separately against a disposable
instance, per README "How to run tests")."""
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "b9c0d1e2f3a4"


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _source() -> str:
    return (BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_wikipedia_lookups.py").read_text()


def test_the_migration_is_the_single_head_on_top_of_the_previous_one():
    scripts = _scripts()
    assert scripts.get_revision(REVISION).down_revision == "a8b9c0d1e2f3"
    # Single-head-without-pinning-which-one pattern (see
    # test_ingredient_language_provenance_migration.py).
    assert len(scripts.get_heads()) == 1


def test_the_migration_only_adds_an_enum_value_and_one_table():
    source = _source()
    assert "ALTER TYPE ingredient_source ADD VALUE IF NOT EXISTS 'WIKIPEDIA_API'" in source
    assert source.count("op.create_table(") == 1 and '"ingredient_wikipedia_lookups"' in source
    # Additive only: no existing column, table, or index is altered or dropped.
    for forbidden in ("op.add_column", "op.alter_column", "op.rename_table", "op.drop_column"):
        assert forbidden not in source
    assert source.count("op.execute(") == 1  # only the ADD VALUE statement
    # Reversible: the created index and table are both dropped.
    assert source.count("op.create_index(") == source.count("op.drop_index(") == 1
    assert 'op.drop_table("ingredient_wikipedia_lookups")' in source
    # The identity link survives an identity deletion.
    assert 'sa.ForeignKey("ingredients.id", ondelete="CASCADE")' in source
    # Closed vocabulary and one-row-per-ingredient dedup are enforced by
    # the database, not just the application code.
    assert "ck_ingredient_wikipedia_lookups_match_status" in source
    assert "uq_ingredient_wikipedia_lookups_ingredient_id" in source
    # Downgrade cannot remove the enum value (Postgres has no `ALTER
    # TYPE ... DROP VALUE`) -- documented, not silently skipped.
    downgrade_body = source.split("def downgrade")[1]
    assert "op.execute" not in downgrade_body  # never actually runs an ALTER TYPE
    assert "DROP VALUE" in downgrade_body  # but documents why it can't


def test_the_model_and_the_migration_declare_the_same_columns():
    from app.models.ingredient_wikipedia_lookup import IngredientWikipediaLookup

    source = _source()
    migrated = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', source))
    assert migrated == {c.name for c in IngredientWikipediaLookup.__table__.columns}
