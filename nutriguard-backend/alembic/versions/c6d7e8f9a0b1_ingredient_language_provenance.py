"""ingredient language provenance, identity-uncertainty flags, and
additive intake-guidance fields

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
"""

from alembic import op
import sqlalchemy as sa


revision = "c6d7e8f9a0b1"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- products: preserve the true pre-translation label/OCR text
    # separately from `raw_ingredient_text` (which stays the CANONICAL,
    # EN/BG, identity-bearing text used to re-derive synthetic ingredient
    # ids -- see `app.services.ocr_normalizer.reconstruct_synthetic_ingredient`).
    op.add_column(
        "products",
        sa.Column("original_ingredient_text", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "products",
        sa.Column("ingredient_text_source_language", sa.String(length=16), nullable=True),
    )

    # --- ingredients: identity-uncertainty (suspected OCR concatenation/
    # ambiguous segmentation -- translation alone must never certify
    # identity for these) plus two additive, non-duplicative info-contract
    # fields (conditions under which an effect applies; general intake
    # guidance distinct from the formal per-kg-bodyweight ADI).
    op.add_column(
        "ingredients",
        sa.Column("identity_uncertain", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "ingredients",
        sa.Column("uncertainty_reason", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ingredients",
        sa.Column("effect_conditions", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "ingredients",
        sa.Column("dietary_guidance", sa.Text(), nullable=False, server_default=""),
    )

    # --- ingredient_localizations: mirror the same two new additive
    # fields so a reviewed Bulgarian profile can localize them too, via
    # the existing `LOCALIZED_FIELDS`/`build_localizations` machinery.
    op.add_column(
        "ingredient_localizations",
        sa.Column("effect_conditions", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "ingredient_localizations",
        sa.Column("dietary_guidance", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("ingredient_localizations", "dietary_guidance")
    op.drop_column("ingredient_localizations", "effect_conditions")

    op.drop_column("ingredients", "dietary_guidance")
    op.drop_column("ingredients", "effect_conditions")
    op.drop_column("ingredients", "uncertainty_reason")
    op.drop_column("ingredients", "identity_uncertain")

    op.drop_column("products", "ingredient_text_source_language")
    op.drop_column("products", "original_ingredient_text")
