from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "b5c6d7e8f9a0"


def test_ingredient_localization_migration_is_the_single_head_and_reversible():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "a4b5c6d7e8f9"
    assert scripts.get_current_head() == REVISION

    source = (BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_localizations.py").read_text()
    assert '"ingredient_localizations"' in source
    assert 'sa.PrimaryKeyConstraint("ingredient_id", "language")' in source
    assert "ondelete=\"CASCADE\"" in source
    assert "ck_ingredient_localizations_language" in source
    assert 'op.drop_table("ingredient_localizations")' in source
