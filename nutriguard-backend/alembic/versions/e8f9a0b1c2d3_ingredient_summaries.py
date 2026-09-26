"""ingredient summaries (source-backed, sectioned, EN with reviewed-gated BG)

Revision ID: e8f9a0b1c2d3
Revises: c6d7e8f9a0b1

Issue #23, stage 3. Purely additive: two new tables. No existing table,
column or row is touched.

INTEGRATION NOTE: this branch is based on `origin/main`, whose head is
`c6d7e8f9a0b1`. The stage-2 branch (ingredient candidate queue) adds
`d7e8f9a0b1c2` on the same parent. Whichever merges second must change its
`down_revision` to the other's revision; the single-head test fails until
that one-line edit is made.
"""

from alembic import op
import sqlalchemy as sa


revision = "e8f9a0b1c2d3"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingredient_summaries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("subject_key", sa.String(length=96), nullable=False),
        sa.Column("scope", sa.String(length=24), nullable=False),
        sa.Column("evidence_state", sa.String(length=16), nullable=False, server_default="DRAFT"),
        sa.Column("human_reviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sections_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("citations_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("claims_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("subject_key", name="uq_ingredient_summaries_subject_key"),
        sa.CheckConstraint("scope IN ('E_NUMBER_GENERIC', 'INGREDIENT_NAME')", name="ck_ingredient_summaries_scope"),
        sa.CheckConstraint(
            "evidence_state IN ('DRAFT', 'SOURCE_VERIFIED')", name="ck_ingredient_summaries_evidence_state"
        ),
    )
    op.create_table(
        "ingredient_summary_localizations",
        sa.Column(
            "summary_id",
            sa.Integer(),
            sa.ForeignKey("ingredient_summaries.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("language", sa.String(length=2), primary_key=True),
        sa.Column("sections_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("translation_status", sa.String(length=12), nullable=False, server_default="DRAFT"),
        sa.Column("translation_source", sa.String(length=24), nullable=False, server_default="MACHINE_TRANSLATED"),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("translation_status IN ('DRAFT', 'REVIEWED')", name="ck_ingredient_summary_loc_status"),
        sa.CheckConstraint(
            "translation_source IN ('MACHINE_TRANSLATED', 'HUMAN_CURATED')", name="ck_ingredient_summary_loc_source"
        ),
        sa.CheckConstraint("language IN ('bg')", name="ck_ingredient_summary_loc_language"),
    )


def downgrade() -> None:
    op.drop_table("ingredient_summary_localizations")
    op.drop_table("ingredient_summaries")
