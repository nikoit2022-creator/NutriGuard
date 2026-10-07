from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "a8b9c0d1e2f3"


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_the_migration_is_the_current_single_head_on_top_of_the_candidate_queue():
    scripts = _scripts()
    assert scripts.get_revision(REVISION).down_revision == "d7e8f9a0b1c2"
    # Single-head-without-pinning-which-one pattern (see
    # test_ingredient_language_provenance_migration.py /
    # test_ingredient_candidate_queue_migration.py) -- a later migration
    # may sit on top of this one without breaking this test.
    assert len(scripts.get_heads()) == 1
    assert REVISION in {rev.revision for rev in scripts.walk_revisions()}


def test_the_migration_only_adds_one_nullable_safe_boolean_column():
    source = (
        BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_localization_owner_approval.py"
    ).read_text()
    assert source.count("op.add_column(") == 1
    assert '"ingredient_localizations"' in source
    assert '"owner_approved_without_review"' in source
    assert "nullable=False" in source and "server_default=sa.false()" in source
    # Additive only in `upgrade()` -- no existing column, table, enum or
    # index is touched; `downgrade()` reverses it with a single drop.
    upgrade_body = source.split("def upgrade")[1].split("def downgrade")[0]
    for forbidden in ("op.drop_column", "op.alter_column", "op.create_table", "op.drop_table", "op.execute"):
        assert forbidden not in upgrade_body
    assert 'op.drop_column("ingredient_localizations", "owner_approved_without_review")' in source
