"""owner-approved-without-review flag for ingredient localizations

Revision ID: a8b9c0d1e2f3
Revises: d7e8f9a0b1c2

docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md: a narrowly scoped,
explicit owner-approved-publication escape hatch for serving DRAFT
localized content (never REVIEWED/HUMAN_CURATED) when an owner has
explicitly authorized that specific content for display without a
human translator review -- see
app.services.ingredient_localization.build_localizations and
app.models.ingredient_localization.IngredientLocalization. Purely
additive: one new, non-nullable boolean column with a `false` default,
so every existing row (and every row written by any other, unrelated
writer going forward) keeps today's exact behavior unless a narrow,
allowlisted importer explicitly sets it.
"""

from alembic import op
import sqlalchemy as sa


revision = "a8b9c0d1e2f3"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingredient_localizations",
        sa.Column(
            "owner_approved_without_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("ingredient_localizations", "owner_approved_without_review")
