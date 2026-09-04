from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Ingredient(Base):
    """
    Mirrors com.example.data.model.IngredientEntity and the
    `scientific_ingredients` table sketched in ArchitectureAdminScreen.kt.

    This is the ONE persistent, reusable ingredient catalog -- every
    product references rows here by id (`Product.ingredient_ids`,
    comma-separated), never a copy of the scientific profile. That now
    includes an OCR/Gemini-observed ingredient with no curated match:
    see `app.services.ingredient_catalog` -- a minimal, honestly-empty
    row (`verification_status=UNVERIFIED`, no fabricated scientific/
    regulatory fields, `risk_level=SAFE`) is get-or-created for it,
    keyed by a normalized-name alias (`IngredientAlias`) so a later scan
    of the SAME ingredient (any recognized language/spelling variant,
    or the same E-number) reuses this ONE row instead of creating
    another. See requirement 2 ("canonical identity") in the
    persistent-ingredient-knowledge-cache task.
    """

    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    common_name: Mapped[str] = mapped_column("common_name", String(255), nullable=False, index=True)
    # Canonical, comparable form of `common_name` (see
    # `app.services.ingredient_normalization.normalize_ingredient_name`)
    # -- the fallback identity key used when no official identifier
    # (E-number/INS/CAS) is available. Always kept in sync with
    # `common_name` at write time; not itself unique (multiple curated
    # rows could theoretically normalize to very similar text), but
    # `IngredientAlias.alias_normalized` -- which always includes this
    # ingredient's own name as one of its aliases -- IS globally unique
    # and is the actual lookup key `ingredient_catalog` uses.
    normalized_name: Mapped[str] = mapped_column(
        "normalized_name", String(255), nullable=False, default="", index=True
    )
    scientific_name: Mapped[str] = mapped_column("scientific_name", String(255), nullable=False, default="")
    e_number: Mapped[str | None] = mapped_column("e_number", String(16), unique=True, nullable=True, index=True)
    # International Numbering System code (Codex Alimentarius) -- for
    # most food additives numerically identical to the E-number's own
    # digits (the EU's E-number scheme is built on INS), so it is safe
    # to derive from a genuine E-number rather than fabricated (see
    # `ingredient_catalog.derive_ins_number_from_e_number`); left null
    # rather than guessed when there is no E-number to derive it from.
    ins_number: Mapped[str | None] = mapped_column("ins_number", String(16), unique=True, nullable=True, index=True)
    # Chemical Abstracts Service registry number. Structural support
    # only for now -- not populated for the curated seed data in this
    # change (see docs/CODEX_HANDOFF.md); a wrong CAS number would be
    # exactly the kind of fabricated identifier this project's data-
    # quality work exists to prevent, so it is only ever set from a
    # verified source, never guessed.
    cas_number: Mapped[str | None] = mapped_column("cas_number", String(32), unique=True, nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose_in_food: Mapped[str] = mapped_column("purpose_in_food", Text, nullable=False, default="")
    health_concerns: Mapped[str] = mapped_column("health_concerns", Text, nullable=False, default="")
    evidence_level: Mapped[str] = mapped_column("evidence_level", String(255), nullable=False, default="")
    countries_restricted_or_banned: Mapped[str] = mapped_column(
        "countries_restricted_or_banned", Text, nullable=False, default=""
    )
    efsa_status: Mapped[str] = mapped_column("efsa_status", String(255), nullable=False, default="")
    fda_status: Mapped[str] = mapped_column("fda_status", String(255), nullable=False, default="")
    who_iarc_classification: Mapped[str | None] = mapped_column(
        "who_iarc_classification", String(255), nullable=True
    )
    acceptable_daily_intake: Mapped[str] = mapped_column(
        "acceptable_daily_intake", String(128), nullable=False, default=""
    )
    side_effects: Mapped[str] = mapped_column("side_effects", Text, nullable=False, default="")
    allergens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    references: Mapped[str] = mapped_column(Text, nullable=False, default="")
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, name="risk_level", native_enum=True), nullable=False
    )
    # True only when `verification_status == VERIFIED` (kept in sync at
    # write time by `app.services.ingredient_catalog`/`app.seed.load_seed`
    # -- not DB-computed) -- a genuinely unassessed ingredient (VERIFIED's
    # negation) can never be confused with a real risk assessment on the
    # wire (see `IngredientOut.risk_assessment_available`/`risk_rationale`).
    risk_assessment_available: Mapped[bool] = mapped_column(
        "risk_assessment_available", Boolean, nullable=False, default=True
    )

    # --- Verification status + provenance (persistent ingredient
    # knowledge cache -- see app.services.ingredient_catalog) ---
    # Record-level provenance, not per-field: every scientific/
    # regulatory field on a given row was written together, from one
    # source, at one point in time (a bulk curated-seed load, or one
    # OCR/Gemini observation) -- there is no per-field mixed-provenance
    # case in this codebase to track, so one set of provenance columns
    # per row is the accurate model, not an arbitrary simplification
    # (mirrors `ProductSource`'s own per-record provenance design).
    verification_status: Mapped[IngredientVerificationStatus] = mapped_column(
        "verification_status",
        Enum(IngredientVerificationStatus, name="ingredient_verification_status", native_enum=True),
        nullable=False,
        default=IngredientVerificationStatus.UNVERIFIED,
    )
    source: Mapped[IngredientSource] = mapped_column(
        "source",
        Enum(IngredientSource, name="ingredient_source", native_enum=True),
        nullable=False,
        default=IngredientSource.OCR_HEURISTIC,
    )
    # Id/URL into that source, when one exists and is safe to store (a
    # curated seed entry's own id, a future regulatory database's
    # record id/URL) -- never a full request/response body or an image.
    source_record_id: Mapped[str | None] = mapped_column("source_record_id", String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column("source_url", String(1024), nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column("retrieved_at", DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        "last_verified_at", DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float] = mapped_column("confidence", Numeric(4, 3), nullable=False, default=0)
    schema_version: Mapped[int] = mapped_column("schema_version", Integer, nullable=False, default=1)

    is_gluten: Mapped[bool] = mapped_column("is_gluten", Boolean, default=False)
    is_lactose: Mapped[bool] = mapped_column("is_lactose", Boolean, default=False)
    is_vegan: Mapped[bool] = mapped_column("is_vegan", Boolean, default=True)
    is_vegetarian: Mapped[bool] = mapped_column("is_vegetarian", Boolean, default=True)
    is_halal: Mapped[bool] = mapped_column("is_halal", Boolean, default=True)
    is_kosher: Mapped[bool] = mapped_column("is_kosher", Boolean, default=True)

    bad_for_diabetes: Mapped[bool] = mapped_column("bad_for_diabetes", Boolean, default=False)
    bad_for_hypertension: Mapped[bool] = mapped_column("bad_for_hypertension", Boolean, default=False)
    bad_for_kidney_disease: Mapped[bool] = mapped_column("bad_for_kidney_disease", Boolean, default=False)
    bad_for_gout: Mapped[bool] = mapped_column("bad_for_gout", Boolean, default=False)
    bad_for_pregnancy: Mapped[bool] = mapped_column("bad_for_pregnancy", Boolean, default=False)
    bad_for_children: Mapped[bool] = mapped_column("bad_for_children", Boolean, default=False)
    bad_for_high_cholesterol: Mapped[bool] = mapped_column("bad_for_high_cholesterol", Boolean, default=False)
