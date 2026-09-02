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

AMENDMENT (PR #9 review round 5, finding 2), while this migration was
still unmerged: `has_verified_ingredients` was originally added with
server_default=true (matching the fail-OPEN default `is_verified`/
`has_verified_nutrition` were already given, further back, by
cf5522508f9a) -- reviewed and found unsafe, for the same reason as the
`Product` model change in this same round: a future INSERT that omits
one of these columns should never silently produce verified,
Health-Score-eligible evidence. This revision now:
  1. Adds `has_verified_ingredients` with a server_default of `false`
     (not `true`) -- still satisfies NOT NULL for the ADD COLUMN step,
     but is now also its correct, final, fail-safe steady-state
     default, so no separate "temporary then final default" step is
     needed for this column.
  2. Runs the exact same conservative backfill as before (copies
     `has_verified_nutrition`'s actual per-row DATA, not the column
     default -- a data backfill and a column default are independent;
     changing the default does not change any already-written value).
  3. ALTERs the server default of `is_verified` and
     `has_verified_nutrition` (both added by the earlier, already-
     authored `cf5522508f9a` migration, deliberately left untouched
     itself) from `true` to `false` -- fixing the same fail-open gap
     for the two pre-existing columns, without rewriting a migration
     that predates this review round. This changes only the DEFAULT
     applied to a future bare INSERT that omits the column; it does not
     touch any existing row's stored value.
`downgrade()` restores the exact pre-this-migration schema: both
defaults back to `true` (as `cf5522508f9a` originally set them), then
drops `has_verified_ingredients` entirely.
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
    # for every row that already existed before this migration. `false`
    # is also this column's correct FINAL steady-state default (review
    # round 5, finding 2) -- fail-safe, not just a placeholder.
    op.add_column(
        'products',
        sa.Column('has_verified_ingredients', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.execute("UPDATE products SET has_verified_ingredients = has_verified_nutrition")

    # Review round 5, finding 2: close the same fail-open gap for the
    # two verification columns `cf5522508f9a` already added with
    # server_default=true. Data-preserving -- only changes what a
    # FUTURE bare INSERT that omits the column receives; every existing
    # row's actual stored value (set above / already present) is
    # untouched.
    op.alter_column('products', 'is_verified', server_default=sa.text('false'))
    op.alter_column('products', 'has_verified_nutrition', server_default=sa.text('false'))


def downgrade() -> None:
    # Restore the exact pre-a1b2c3d4e5f6 schema: `is_verified`/
    # `has_verified_nutrition` defaults back to `true` (as
    # `cf5522508f9a` originally set them), then drop
    # `has_verified_ingredients` entirely.
    op.alter_column('products', 'has_verified_nutrition', server_default=sa.text('true'))
    op.alter_column('products', 'is_verified', server_default=sa.text('true'))
    op.drop_column('products', 'has_verified_ingredients')
