"""ingredient candidate queue (bounded observation metadata)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1

Issue #23, stage 2. Purely additive: one new table. No existing table,
column, alias or product reference is touched, and no data is backfilled
here (a manual, dry-run-by-default command does that; see
docs/INGREDIENT_CANDIDATE_QUEUE.md).
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredient_candidates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("normalized_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("e_number", sa.String(length=16), nullable=True),
        sa.Column(
            "ingredient_id",
            sa.String(length=64),
            sa.ForeignKey("ingredients.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("flags", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("encounter_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("normalized_key", name="uq_ingredient_candidates_normalized_key"),
        sa.CheckConstraint("status IN ('PENDING', 'FLAGGED', 'JUNK')", name="ck_ingredient_candidates_status"),
        sa.CheckConstraint("encounter_count >= 1", name="ck_ingredient_candidates_encounter_count"),
    )
    op.create_index("ix_ingredient_candidates_e_number", "ingredient_candidates", ["e_number"])
    op.create_index("ix_ingredient_candidates_ingredient_id", "ingredient_candidates", ["ingredient_id"])
    op.create_index("ix_ingredient_candidates_status", "ingredient_candidates", ["status"])
    op.create_index("ix_ingredient_candidates_last_seen_at", "ingredient_candidates", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_ingredient_candidates_last_seen_at", table_name="ingredient_candidates")
    op.drop_index("ix_ingredient_candidates_status", table_name="ingredient_candidates")
    op.drop_index("ix_ingredient_candidates_ingredient_id", table_name="ingredient_candidates")
    op.drop_index("ix_ingredient_candidates_e_number", table_name="ingredient_candidates")
    op.drop_table("ingredient_candidates")
