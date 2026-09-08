from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "f5a6b7c8d9e0_nullable_ingredient_dietary_flags.py"

_DIETARY_FLAG_COLUMNS = ("is_gluten", "is_lactose", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")


def test_nullable_ingredient_dietary_flags_migration_is_additive_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "e4f5a6b7c8d9"' in source

    assert "def upgrade" in source
    assert "def downgrade" in source

    # Every one of the six dietary-identity columns is touched, and only
    # those six -- `bad_for_*` and Product's own is_gluten_free/etc. are
    # a deliberately separate concern (see the migration's own module
    # docstring) and must not appear here.
    for column in _DIETARY_FLAG_COLUMNS:
        assert f'"{column}"' in source
    for untouched in ("bad_for_diabetes", "is_gluten_free", "is_lactose_free"):
        assert untouched not in source

    assert "nullable=True" in source
    assert "nullable=False" in source  # only in downgrade()


def test_nullable_ingredient_dietary_flags_migration_backfills_before_re_adding_not_null():
    """The downgrade path must never attempt to re-add a NOT NULL
    constraint while a real NULL (an UNVERIFIED row written after the
    upgrade) still exists in the column -- that would fail outright
    against a real Postgres instance with pre-existing data."""
    source = MIGRATION.read_text(encoding="utf-8")
    downgrade_body = source.split("def downgrade")[1]
    assert "IS NULL" in downgrade_body
    assert "= false" in downgrade_body
