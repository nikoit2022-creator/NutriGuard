from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "b2c3d4e5f6a7_add_nutrition_basis.py"


def test_nutrition_basis_migration_is_additive_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "a1b2c3d4e5f6"' in source
    assert '"nutrition_basis"' in source
    assert '"serving_size"' in source
    assert '"serving_unit"' in source
    assert "WHERE has_verified_nutrition = true" in source
    assert 'drop_column("products", "nutrition_basis")' in source
