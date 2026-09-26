from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "c6d7e8f9a0b1"


def test_ingredient_language_provenance_migration_is_in_the_single_head_chain_and_reversible():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "b5c6d7e8f9a0"
    # Still part of the one linear chain (a later migration may sit on top).
    assert len(scripts.get_heads()) == 1
    assert REVISION in {rev.revision for rev in scripts.walk_revisions()}

    source = (
        BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_ingredient_language_provenance.py"
    ).read_text()
    for column in (
        '"original_ingredient_text"',
        '"ingredient_text_source_language"',
        '"identity_uncertain"',
        '"uncertainty_reason"',
        '"effect_conditions"',
        '"dietary_guidance"',
    ):
        assert column in source
    # Reversible: every added column has a matching drop in downgrade().
    assert source.count("op.add_column") == source.count("op.drop_column")
