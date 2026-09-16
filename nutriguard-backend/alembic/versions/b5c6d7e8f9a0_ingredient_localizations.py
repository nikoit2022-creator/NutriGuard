"""reviewed ingredient display localizations

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None

translation_status = sa.Enum("DRAFT", "REVIEWED", name="ingredient_translation_status")
translation_source = sa.Enum(
    "HUMAN_CURATED", "MACHINE_TRANSLATED", name="ingredient_translation_source"
)
translation_status_column = postgresql.ENUM(
    "DRAFT", "REVIEWED", name="ingredient_translation_status", create_type=False
)
translation_source_column = postgresql.ENUM(
    "HUMAN_CURATED", "MACHINE_TRANSLATED", name="ingredient_translation_source", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    translation_status.create(bind, checkfirst=True)
    translation_source.create(bind, checkfirst=True)
    op.create_table(
        "ingredient_localizations",
        sa.Column("ingredient_id", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("common_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("purpose_in_food", sa.Text(), nullable=False, server_default=""),
        sa.Column("health_concerns", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_level", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("countries_restricted_or_banned", sa.Text(), nullable=False, server_default=""),
        sa.Column("efsa_status", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("fda_status", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("acceptable_daily_intake", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("side_effects", sa.Text(), nullable=False, server_default=""),
        sa.Column("allergens", sa.Text(), nullable=False, server_default=""),
        sa.Column("translation_status", translation_status_column, nullable=False),
        sa.Column("translation_source", translation_source_column, nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("language IN ('en', 'bg')", name="ck_ingredient_localizations_language"),
        sa.ForeignKeyConstraint(["ingredient_id"], ["ingredients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ingredient_id", "language"),
    )
    op.create_index(
        "ix_ingredient_localizations_language", "ingredient_localizations", ["language"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ingredient_localizations_language", table_name="ingredient_localizations")
    op.drop_table("ingredient_localizations")
    bind = op.get_bind()
    translation_source.drop(bind, checkfirst=True)
    translation_status.drop(bind, checkfirst=True)
