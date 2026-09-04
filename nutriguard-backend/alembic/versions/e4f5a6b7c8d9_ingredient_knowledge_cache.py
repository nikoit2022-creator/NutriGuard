"""persistent ingredient knowledge cache: catalog provenance + aliases

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Canonical-identity columns -----------------------------------
    op.add_column("ingredients", sa.Column("normalized_name", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("ingredients", sa.Column("ins_number", sa.String(length=16), nullable=True))
    op.create_index("ix_ingredients_ins_number", "ingredients", ["ins_number"], unique=True)
    op.add_column("ingredients", sa.Column("cas_number", sa.String(length=32), nullable=True))
    op.create_index("ix_ingredients_cas_number", "ingredients", ["cas_number"], unique=True)

    # --- Verification status + record-level provenance ----------------
    ingredient_verification_status = postgresql.ENUM(
        "VERIFIED", "LIMITED_DATA", "UNVERIFIED", name="ingredient_verification_status"
    )
    ingredient_verification_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "ingredients",
        sa.Column(
            "verification_status",
            ingredient_verification_status,
            nullable=False,
            server_default="UNVERIFIED",
        ),
    )
    ingredient_source = postgresql.ENUM(
        "CURATED_SEED", "REGULATORY_LOOKUP", "GEMINI", "OCR_HEURISTIC", name="ingredient_source"
    )
    ingredient_source.create(op.get_bind(), checkfirst=True)
    # Reused below for `ingredient_aliases.source` -- the type itself
    # was just created; tell SQLAlchemy not to try to CREATE TYPE again
    # when this same Python object is used in `create_table`.
    ingredient_source.create_type = False
    op.add_column(
        "ingredients",
        sa.Column("source", ingredient_source, nullable=False, server_default="OCR_HEURISTIC"),
    )
    op.add_column("ingredients", sa.Column("source_record_id", sa.String(length=255), nullable=True))
    op.add_column("ingredients", sa.Column("source_url", sa.String(length=1024), nullable=True))
    op.add_column("ingredients", sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ingredients", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("ingredients", sa.Column("confidence", sa.Numeric(4, 3), nullable=False, server_default="0"))
    op.add_column("ingredients", sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"))

    # --- Backfill: every row that already exists at this point is
    # curated/seeded data (this migration predates any persisted
    # OCR-observed row -- see app.services.ingredient_catalog, added in
    # the same change), so VERIFIED/CURATED_SEED/full confidence is the
    # correct value for every existing row, not just a placeholder.
    op.execute(
        "UPDATE ingredients SET "
        "normalized_name = lower(trim(both ' ' from common_name)), "
        "verification_status = 'VERIFIED', "
        "source = 'CURATED_SEED', "
        "retrieved_at = now(), "
        "last_verified_at = now(), "
        "confidence = 1.000"
    )
    # INS numbers are, for essentially every food additive shared
    # between the two systems, numerically identical to the E-number's
    # own digits (the EU E-number scheme is built directly on the Codex
    # Alimentarius INS numbering) -- a safe, mechanical derivation from
    # an already-verified identifier, not a fabricated one. Left null
    # (unchanged) for the curated rows with no E-number at all.
    op.execute("UPDATE ingredients SET ins_number = substring(e_number from 2) WHERE e_number IS NOT NULL")

    # --- Alias table -----------------------------------------------------
    op.create_table(
        "ingredient_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ingredient_id",
            sa.String(length=64),
            sa.ForeignKey("ingredients.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("alias_text", sa.String(length=255), nullable=False),
        sa.Column("alias_normalized", sa.String(length=255), nullable=False, unique=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("source", ingredient_source, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ingredient_aliases_ingredient_id", "ingredient_aliases", ["ingredient_id"])
    op.create_index(
        "ix_ingredient_aliases_alias_normalized", "ingredient_aliases", ["alias_normalized"], unique=True
    )


def downgrade() -> None:
    op.drop_table("ingredient_aliases")

    op.drop_column("ingredients", "schema_version")
    op.drop_column("ingredients", "confidence")
    op.drop_column("ingredients", "last_verified_at")
    op.drop_column("ingredients", "retrieved_at")
    op.drop_column("ingredients", "source_url")
    op.drop_column("ingredients", "source_record_id")
    op.drop_column("ingredients", "source")
    op.drop_column("ingredients", "verification_status")
    ingredient_source = postgresql.ENUM(name="ingredient_source")
    ingredient_source.drop(op.get_bind(), checkfirst=True)
    ingredient_verification_status = postgresql.ENUM(name="ingredient_verification_status")
    ingredient_verification_status.drop(op.get_bind(), checkfirst=True)

    op.drop_index("ix_ingredients_cas_number", table_name="ingredients")
    op.drop_column("ingredients", "cas_number")
    op.drop_index("ix_ingredients_ins_number", table_name="ingredients")
    op.drop_column("ingredients", "ins_number")
    op.drop_column("ingredients", "normalized_name")
