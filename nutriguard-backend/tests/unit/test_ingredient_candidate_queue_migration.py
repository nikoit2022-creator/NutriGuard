"""Issue #23 stage 2: migration d7e8f9a0b1c2 is one linear, additive,
reversible step on top of c6d7e8f9a0b1 (real upgrade/downgrade on
PostgreSQL is exercised separately against a disposable instance)."""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "d7e8f9a0b1c2"


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_the_migration_is_the_single_head_on_top_of_the_previous_one():
    scripts = _scripts()
    assert scripts.get_revision(REVISION).down_revision == "c6d7e8f9a0b1"
    assert scripts.get_heads() == [REVISION]


def test_the_migration_only_adds_one_table_and_reverses_it():
    source = (BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_candidate_queue.py").read_text()
    assert source.count("op.create_table(") == 1 and '"ingredient_candidates"' in source
    # Additive: nothing existing is altered, dropped, or rewritten.
    for forbidden in ("op.add_column", "op.alter_column", "op.execute", "op.rename_table", "op.drop_column"):
        assert forbidden not in source
    # Reversible: every created index has a matching drop, and the table is dropped.
    assert source.count("op.create_index(") == source.count("op.drop_index(") == 4
    assert 'op.drop_table("ingredient_candidates")' in source
    # The identity link survives an identity deletion.
    assert 'sa.ForeignKey("ingredients.id", ondelete="SET NULL")' in source
    # Closed vocabulary and positive counter are enforced by the database, not just the code.
    assert "ck_ingredient_candidates_status" in source and "ck_ingredient_candidates_encounter_count" in source
    assert "uq_ingredient_candidates_normalized_key" in source


def test_the_model_and_the_migration_declare_the_same_columns():
    import re

    from app.models.ingredient_candidate import IngredientCandidate

    source = (BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_candidate_queue.py").read_text()
    migrated = set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', source))
    assert migrated == {c.name for c in IngredientCandidate.__table__.columns}
