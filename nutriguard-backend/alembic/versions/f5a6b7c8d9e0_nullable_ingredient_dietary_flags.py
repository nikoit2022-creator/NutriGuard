"""nullable ingredient dietary-identity flags: no fabricated vegan/halal/etc

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9

Review blocker (PR #13): an UNVERIFIED OCR/Gemini-observed `ingredients`
row used to default `is_vegan`/`is_vegetarian`/`is_halal`/`is_kosher` to
`True` and `is_gluten`/`is_lactose` to `False` -- every one of those is
a fabricated positive certification/safety claim for an ingredient no
scientific database ever actually assessed (see
`app.services.ocr_normalizer.SyntheticIngredient`). These six columns
become nullable so "genuinely unknown" has an honest value of its own
(`NULL`) instead of being forced into whichever boolean happened to be
the "safe-looking" default. This is additive/backward compatible: every
EXISTING row's actual True/False value is left completely untouched --
only the column-level NOT NULL constraint is lifted, nothing is
rewritten.

`bad_for_*` (diabetes/hypertension/kidney disease/gout/pregnancy/
children/high cholesterol) are UNCHANGED here -- `False` there already
means "not flagged" (the safe, non-alarming direction for a fabricated-
risk claim; a false positive there would have driven a real HIGH-
severity personalized warning), so no schema change is needed for that
group. Only the six dietary-IDENTITY columns above are affected, since
for THOSE the "safe-looking" default was actually the dangerous
direction (a fabricated certification, not the absence of a warning).
"""

from alembic import op
import sqlalchemy as sa


revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None

_DIETARY_FLAG_COLUMNS = (
    "is_gluten",
    "is_lactose",
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
)


def upgrade() -> None:
    for column in _DIETARY_FLAG_COLUMNS:
        op.alter_column("ingredients", column, existing_type=sa.Boolean(), nullable=True)


def downgrade() -> None:
    # A real `NULL` written after this migration's upgrade (an
    # UNVERIFIED OCR/Gemini row with a genuinely unknown status) has no
    # honest boolean equivalent to roll back to -- `False` is the least-
    # asserting choice available (see this file's own module docstring:
    # "not flagged" rather than a positive certification), so it is the
    # backfill used here, exactly like this same column's ORIGINAL
    # (pre-this-migration) NOT NULL default already was for `is_gluten`/
    # `is_lactose`. This does lose the "unknown" distinction for any row
    # written between upgrade and downgrade -- an unavoidable, and
    # clearly documented, consequence of downgrading past the fix this
    # migration exists to make.
    for column in _DIETARY_FLAG_COLUMNS:
        op.execute(f"UPDATE ingredients SET {column} = false WHERE {column} IS NULL")
        op.alter_column("ingredients", column, existing_type=sa.Boolean(), nullable=False)
