"""
Structural checks on the `a1b2c3d4e5f6` "split nutrition/ingredients
verification flags" Alembic migration (review round 4, finding 1): a
valid parent revision, upgrade/downgrade functions that are actually
present, and the expected DDL. Mirrors
`test_barcode_discovery_migration.py`'s approach -- this does not
execute DDL (see that file's docstring for the project's convention);
the real Postgres upgrade -> downgrade -> upgrade cycle for this
migration was run manually against a disposable container -- see the
PR description/final report for the transcript.

`test_migration_chain_has_a_single_head` (in
`test_barcode_discovery_migration.py`) already covers the whole chain
dynamically, so it doesn't need duplicating here.
"""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "a1b2c3d4e5f6"


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_migration_has_the_current_single_head_as_its_parent():
    script = _script_directory()
    revision = script.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == "cf5522508f9a"
    assert script.get_revision(revision.down_revision) is not None
    # The additive nutrition-basis migration must extend this revision,
    # preserving a single linear head rather than branching from it.
    assert list(script.get_heads()) == ["b2c3d4e5f6a7"]
    assert script.get_revision("b2c3d4e5f6a7").down_revision == REVISION


def test_migration_defines_upgrade_and_downgrade():
    script = _script_directory()
    revision = script.get_revision(REVISION)
    module = revision.module
    assert callable(getattr(module, "upgrade", None))
    assert callable(getattr(module, "downgrade", None))


def test_migration_adds_has_verified_ingredients_with_safe_backfill():
    """Sanity-checks the migration file's own DDL/backfill calls without
    running them, so a future edit that silently drops the column or
    the backfill is caught."""
    source = (
        BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_split_nutrition_ingredients_verification.py"
    ).read_text()
    assert "add_column" in source
    assert "'products'" in source
    assert "has_verified_ingredients" in source
    # NOT NULL on an existing table needs a server_default so existing
    # rows don't break the migration -- `false` (review round 5, finding
    # 2: fail-safe, not fail-open) is BOTH the temporary default needed
    # to satisfy NOT NULL here AND this column's correct final default.
    assert "server_default=sa.text('false')" in source
    # Conservative backfill: copies the OLD model's combined-completeness
    # proxy (has_verified_nutrition) verbatim, never inspects text
    # content or upgrades a row based on a non-empty placeholder value.
    assert "UPDATE products SET has_verified_ingredients = has_verified_nutrition" in source
    assert "def downgrade" in source
    assert "drop_column('products', 'has_verified_ingredients')" in source


def test_migration_flips_the_two_pre_existing_verification_columns_to_fail_safe_defaults():
    """Review round 5, finding 2: `is_verified`/`has_verified_nutrition`
    (added by the earlier `cf5522508f9a` migration with a fail-open
    `server_default=true`) must have their DEFAULT flipped to `false`
    here, and `downgrade()` must restore the exact original `true`
    default for both -- never leave a fail-open default reachable by a
    future bare INSERT after either an upgrade or a downgrade."""
    source = (
        BACKEND_ROOT / "alembic" / "versions" / f"{REVISION}_split_nutrition_ingredients_verification.py"
    ).read_text()
    upgrade_src, downgrade_src = source.split("def downgrade", 1)

    assert "alter_column('products', 'is_verified', server_default=sa.text('false'))" in upgrade_src
    assert "alter_column('products', 'has_verified_nutrition', server_default=sa.text('false'))" in upgrade_src

    assert "alter_column('products', 'has_verified_nutrition', server_default=sa.text('true'))" in downgrade_src
    assert "alter_column('products', 'is_verified', server_default=sa.text('true'))" in downgrade_src
