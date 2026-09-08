from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND_ROOT / "alembic" / "versions" / "e4f5a6b7c8d9_ingredient_knowledge_cache.py"


def test_ingredient_knowledge_cache_migration_is_additive_and_reversible():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'down_revision = "d3e4f5a6b7c8"' in source

    # Canonical-identity columns.
    for column in ('"normalized_name"', '"ins_number"', '"cas_number"'):
        assert column in source

    # Verification status + provenance columns.
    for column in (
        '"verification_status"', '"source"', '"source_record_id"', '"source_url"',
        '"retrieved_at"', '"last_verified_at"', '"confidence"', '"schema_version"',
    ):
        assert column in source

    # Every existing row is curated/seeded at this point in the chain --
    # backfilled to VERIFIED/CURATED_SEED/full confidence, not left at
    # the fail-safe UNVERIFIED/OCR_HEURISTIC column default.
    assert "verification_status = 'VERIFIED'" in source
    assert "source = 'CURATED_SEED'" in source
    assert "confidence = 1.000" in source

    # The alias table itself.
    assert 'op.create_table(\n        "ingredient_aliases"' in source
    assert '"ingredient_id"' in source
    assert 'sa.ForeignKey("ingredients.id", ondelete="CASCADE")' in source
    assert '"alias_normalized"' in source
    assert "unique=True" in source

    assert "def downgrade" in source
    assert 'op.drop_table("ingredient_aliases")' in source
