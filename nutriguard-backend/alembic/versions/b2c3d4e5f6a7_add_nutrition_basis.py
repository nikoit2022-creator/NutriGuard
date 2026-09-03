"""add explicit nutrition basis and serving metadata

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from alembic import op
import sqlalchemy as sa


revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("nutrition_basis", sa.String(length=16), nullable=False, server_default="UNKNOWN"),
    )
    op.add_column("products", sa.Column("serving_size", sa.Numeric(8, 2), nullable=True))
    op.add_column("products", sa.Column("serving_unit", sa.String(length=16), nullable=True))
    # Before this migration every verified nutrition value was defined by
    # the contract as per 100 g. Preserve that meaning for existing rows.
    op.execute(
        "UPDATE products SET nutrition_basis = 'PER_100_G' "
        "WHERE has_verified_nutrition = true"
    )


def downgrade() -> None:
    op.drop_column("products", "serving_unit")
    op.drop_column("products", "serving_size")
    op.drop_column("products", "nutrition_basis")
