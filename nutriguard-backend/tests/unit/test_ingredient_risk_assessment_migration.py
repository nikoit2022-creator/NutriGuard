from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "d3e4f5a6b7c8_add_ingredient_risk_assessment_flag.py"


def test_ingredient_risk_assessment_migration_is_additive_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "b2c3d4e5f6a7"' in source
    assert '"risk_assessment_available"' in source
    assert "nullable=False" in source
    # Every existing row is curated/seeded data (a synthetic OCR-only
    # ingredient is never persisted to this table), so backfilling
    # `true` for pre-existing rows is correct, not just a placeholder.
    assert 'server_default=sa.text("true")' in source
    assert 'drop_column("ingredients", "risk_assessment_available")' in source
