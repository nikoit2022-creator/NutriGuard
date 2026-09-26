"""Issue #23 stage 3: migration e8f9a0b1c2d3 is one linear, additive,
reversible step on top of c6d7e8f9a0b1 (real upgrade/downgrade on
PostgreSQL is exercised separately against a disposable instance)."""
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "e8f9a0b1c2d3"
SOURCE = BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_summaries.py"


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_the_migration_is_the_single_head_on_top_of_the_previous_one():
    scripts = _scripts()
    assert scripts.get_revision(REVISION).down_revision == "c6d7e8f9a0b1"
    assert scripts.get_heads() == [REVISION]


def test_the_migration_only_adds_two_tables_and_reverses_them_in_dependency_order():
    source = SOURCE.read_text()
    assert source.count("op.create_table(") == 2
    assert '"ingredient_summaries"' in source and '"ingredient_summary_localizations"' in source
    # Additive: nothing existing is altered, dropped, or rewritten.
    for forbidden in ("op.add_column", "op.alter_column", "op.execute", "op.rename_table", "op.drop_column"):
        assert forbidden not in source
    down = source[source.index("def downgrade"):]
    assert down.index('"ingredient_summary_localizations"') < down.index('"ingredient_summaries"')
    # Translations follow their summary, and the subject is unique.
    assert 'ondelete="CASCADE"' in source and "uq_ingredient_summaries_subject_key" in source
    # Closed vocabularies are enforced by the database, not just by the code.
    for name in (
        "ck_ingredient_summaries_scope",
        "ck_ingredient_summaries_evidence_state",
        "ck_ingredient_summary_loc_status",
        "ck_ingredient_summary_loc_source",
        "ck_ingredient_summary_loc_language",
    ):
        assert name in source


def test_the_models_and_the_migration_declare_the_same_columns():
    from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization

    source = SOURCE.read_text()
    up = source[source.index("def upgrade"):source.index("def downgrade")]
    first, second = up.split('"ingredient_summary_localizations"', 1)
    assert set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', first)) == {c.name for c in IngredientSummary.__table__.columns}
    assert set(re.findall(r'sa\.Column\(\s*"([a-z_]+)"', second)) == {
        c.name for c in IngredientSummaryLocalization.__table__.columns
    }


def test_the_database_defaults_are_fail_safe():
    """A row inserted by any other path is a DRAFT, unreviewed, machine translation."""
    source = SOURCE.read_text()
    assert 'server_default="DRAFT"' in source and "server_default=sa.false()" in source
    assert 'server_default="MACHINE_TRANSLATED"' in source
