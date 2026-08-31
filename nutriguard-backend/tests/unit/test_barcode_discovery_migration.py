"""
Structural checks on the barcode-discovery Alembic migration: a single
head, a valid parent revision, and upgrade/downgrade functions that are
actually present and internally consistent. This does not execute DDL
(the project's own convention is that real DDL execution against
Postgres is validated separately, not through `pytest` — see
README.md "Running tests"); the corresponding real-Postgres
upgrade -> downgrade -> upgrade cycle for this migration was run
manually via `docker compose run backend alembic ...` — see the PR
description for the transcript.
"""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_migration_chain_has_a_single_head():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1


def test_barcode_discovery_migration_has_a_valid_parent_revision():
    script = _script_directory()
    revision = script.get_revision("cf5522508f9a")
    assert revision is not None
    assert revision.down_revision == "64dfe47cbbf7"
    # The parent revision must actually exist in the chain (not a typo'd id).
    assert script.get_revision(revision.down_revision) is not None


def test_barcode_discovery_migration_defines_upgrade_and_downgrade():
    script = _script_directory()
    revision = script.get_revision("cf5522508f9a")
    module = revision.module
    assert callable(getattr(module, "upgrade", None))
    assert callable(getattr(module, "downgrade", None))


def test_migration_adds_product_sources_table_and_product_provenance_columns():
    """Sanity-checks the migration file's own DDL calls without running
    them, so a future edit that silently drops a required column/table
    is caught."""
    source = (BACKEND_ROOT / "alembic" / "versions" / "cf5522508f9a_barcode_discovery_provenance.py").read_text()
    assert "create_table('product_sources'" in source
    for column in ("source", "source_confidence", "is_verified", "discovered_at", "last_verified_at"):
        assert f"'products', sa.Column('{column}'" in source
    # Backward compatibility: new NOT NULL columns on an existing table
    # must carry a server_default so existing rows don't break the migration.
    assert "server_default='local'" in source
    assert "server_default=sa.text('false')" in source
