"""ingredient per-field scientific/regulatory provenance

Revision ID: a4b5c6d7e8f9
Revises: f5a6b7c8d9e0

Review blocker (PR #13): `merge_verified_fields` accepts a PARTIAL
subset of `app.services.ingredient_catalog._MERGEABLE_FIELDS` (a
regulatory response that only supplies e.g. `description`), but until
now `ingredients` stored only ONE record-level `source`/`confidence`/
`retrieved_at`/`last_verified_at` for ALL of those columns together --
so a partial update could relabel an untouched field (still, in truth,
older/lower-trust content) as if the incoming provider had supplied it
too. `field_provenance_json` is an additive, nullable column: a compact
JSON object recording, per mergeable field that has ever been
independently merged, which source/confidence/timestamp actually wrote
it. See `app.models.ingredient.Ingredient.field_provenance_json`'s own
docstring and `app.services.ingredient_catalog.resolve_field_source`.

Purely additive/backward compatible: every existing row's
`field_provenance_json` starts `NULL` (meaning "every mergeable field's
real origin is this row's own record-level `source`" -- true for every
row that predates this migration, since none of them could have gone
through a partial merge before this column existed to record one).
Nothing else changes; no backfill needed.
"""

from alembic import op
import sqlalchemy as sa


revision = "a4b5c6d7e8f9"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingredients",
        sa.Column("field_provenance_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # Additive-only column -- safe to drop outright, no data migrated
    # elsewhere to preserve (see this file's own module docstring: a
    # `NULL` value here has always meant "defer to the row's own
    # record-level source", which remains true and unaffected by this
    # column's removal).
    op.drop_column("ingredients", "field_provenance_json")
