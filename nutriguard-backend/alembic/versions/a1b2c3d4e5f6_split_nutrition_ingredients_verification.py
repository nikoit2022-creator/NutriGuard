"""split nutrition/ingredients verification flags

Revision ID: a1b2c3d4e5f6
Revises: cf5522508f9a
Create Date: 2026-09-01 12:00:00.000000

PR #9 review round 4: `has_verified_nutrition` was being asked to mean
two different things at once -- "nutrition is genuinely known" AND "the
whole product is complete". This adds `has_verified_ingredients` as its
own column so nutrition and ingredient evidence, which genuinely come
from different parts of a label (and can arrive in different requests --
e.g. a trusted barcode provider's nutrition facts today, the user's own
ingredient-list photo later), are tracked independently.
`is_verified` remains the single "both true" gate.

Backfill (conservative, per the review requirement -- "incomplete/
unknown legacy rows must not be upgraded based only on non-empty
placeholder values"): every existing row was written under the OLD
single-flag model, in which the app's own code always kept
`is_verified == has_verified_nutrition` by construction (see the
pre-this-migration `food_analysis.py`: every write path set
`existing.is_verified = existing.has_verified_nutrition`). So
`has_verified_ingredients = has_verified_nutrition` for every existing
row is not a guess -- it exactly reproduces what the OLD model already
implies about each row's completeness, without inspecting any text
content (no risk of "upgrading" an incomplete row based on a non-empty
placeholder string). A row that was fully verified before stays fully
verified after; a row that was incomplete before stays incomplete
(`has_verified_ingredients=false`) until a future enrichment attempt
running the NEW code genuinely earns it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'cf5522508f9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New rows (inserted by the updated application code, which always
    # supplies an explicit value) get the correct value regardless of
    # this default; the default exists only so this ADD COLUMN can
    # satisfy NOT NULL for pre-existing rows without a table rewrite
    # pass first -- see the backfill UPDATE below, which is authoritative
    # for every row that already existed before this migration.
    op.add_column(
        'products',
        sa.Column('has_verified_ingredients', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    )
    op.execute("UPDATE products SET has_verified_ingredients = has_verified_nutrition")


def downgrade() -> None:
    op.drop_column('products', 'has_verified_ingredients')
