"""Persist explicit dietary tag evidence for product three-state status.

Revision ID: a9c0d1e2f3a4
Revises: a8b9c0d1e2f3
"""

from alembic import op
import sqlalchemy as sa

revision = "a9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("dietary_evidence_json", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("products", "dietary_evidence_json")
