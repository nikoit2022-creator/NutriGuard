"""add ingredient risk_assessment_available flag

Revision ID: d3e4f5a6b7c8
Revises: b2c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa


revision = "d3e4f5a6b7c8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Every existing row in `ingredients` is a curated/seeded scientific
    # entry (an OCR-only "synthetic" ingredient is never persisted to
    # this table -- it is reconstructed in memory on the fly, see
    # `app.services.ocr_normalizer`), so `true` is the correct value for
    # every row that already exists, not just a placeholder default.
    op.add_column(
        "ingredients",
        sa.Column("risk_assessment_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("ingredients", "risk_assessment_available")
