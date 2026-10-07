"""wikipedia fallback: WIKIPEDIA_API ingredient source + lookup provenance/cache table

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3

Purely additive: one new enum value on the existing `ingredient_source`
Postgres enum, and one new table. No existing column, alias, or product
reference is touched, and nothing is backfilled -- every existing
`ingredients` row keeps its current `source`/`verification_status`
exactly as-is.

`ALTER TYPE ... ADD VALUE` is safe to run inside Alembic's normal
transactional migration on Postgres 12+ as long as the new value is not
also USED within the same transaction (it isn't here) -- this project's
Postgres is version 16 (see docker-compose.yml).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ingredient_source ADD VALUE IF NOT EXISTS 'WIKIPEDIA_API'")

    op.create_table(
        "ingredient_wikipedia_lookups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingredient_id",
            sa.String(length=64),
            sa.ForeignKey("ingredients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("query_text", sa.String(length=255), nullable=False),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("review_reason", sa.String(length=32), nullable=True),
        sa.Column("matched_page_title", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("extracted_summary", sa.Text(), nullable=True),
        sa.Column("fields_populated_json", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_detail", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("ingredient_id", name="uq_ingredient_wikipedia_lookups_ingredient_id"),
        sa.CheckConstraint(
            "match_status IN ('MATCHED', 'NOT_FOUND', 'AMBIGUOUS', 'SKIPPED_UNCLEAR_NAME', 'ERROR')",
            name="ck_ingredient_wikipedia_lookups_match_status",
        ),
    )
    op.create_index(
        "ix_ingredient_wikipedia_lookups_ingredient_id", "ingredient_wikipedia_lookups", ["ingredient_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_ingredient_wikipedia_lookups_ingredient_id", table_name="ingredient_wikipedia_lookups")
    op.drop_table("ingredient_wikipedia_lookups")
    # Postgres has no `ALTER TYPE ... DROP VALUE` -- removing 'WIKIPEDIA_API'
    # from `ingredient_source` would require rebuilding the enum type
    # (rename, create new, migrate every dependent column, drop old),
    # which is destructive for a value this migration never backfills
    # onto any row. Deliberately left in place; harmless unused vocabulary
    # on downgrade, never produced by any code once this change is reverted.
