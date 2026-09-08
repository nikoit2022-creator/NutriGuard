from datetime import datetime, timedelta, timezone

from pydantic import Field, computed_field, field_serializer

from app.core.config import settings
from app.models.enums import ApprovalStatus, IngredientSource, IngredientVerificationStatus, RiskLevel
from app.schemas.common import ORMModel
from app.services.ingredient_catalog import resolve_field_source
from app.services.ingredient_regulatory import (
    derive_gated_adi_range_mg_per_kg_bw_per_day,
    derive_gated_approval_status,
)


def _as_utc(value: datetime) -> datetime:
    """SQLite (this test suite's DB) doesn't preserve timezone info on
    a `DateTime(timezone=True)` column -- a value written as UTC comes
    back naive, and both `datetime` subtraction and `.timestamp()`
    silently misbehave (the latter assumes the LOCAL system timezone
    for a naive value) if that's not corrected first. Every timestamp
    this field ever holds IS UTC (see `app.services.ingredient_catalog._utcnow`),
    so treating a naive value as UTC is always correct, never a guess
    -- same pattern as `auth_service`'s `expires_at.replace(tzinfo=timezone.utc)`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class IngredientOut(ORMModel):
    """Mirrors com.example.data.model.IngredientEntity exactly (API Contract 5.4),
    plus additive, backward-compatible data-quality fields (see the
    "backend-ingredient-profile-data-quality" task): the original
    `riskLevel`/`efsaStatus`/`fdaStatus`/`acceptableDailyIntake` text
    fields are preserved unchanged for existing clients; the new fields
    below let an updated client distinguish a genuine assessment from
    an absent one, without ever fabricating one."""

    id: str
    common_name: str
    scientific_name: str
    e_number: str | None = None
    # Codex Alimentarius INS code and CAS registry number -- additional
    # official identifiers (task: "persistent ingredient knowledge
    # cache", requirement 2). `None` whenever not established for this
    # row -- never guessed (see app.services.ingredient_catalog).
    ins_number: str | None = None
    cas_number: str | None = None
    category: str
    description: str
    purpose_in_food: str
    health_concerns: str
    evidence_level: str
    countries_restricted_or_banned: str
    efsa_status: str
    fda_status: str
    who_iarc_classification: str | None = None
    acceptable_daily_intake: str
    side_effects: str
    allergens: str
    references: str
    risk_level: RiskLevel
    # False for an OCR-only ingredient with no scientific-database match
    # (see app.services.ocr_normalizer.SyntheticIngredient) -- `riskLevel`
    # above is then a safe, non-alarming placeholder (SAFE), never a
    # keyword guess, and callers must not treat it as an assessment.
    risk_assessment_available: bool = True

    # --- Provenance (persistent ingredient knowledge cache, requirement 3) ---
    # Record-level -- see `app.models.ingredient.Ingredient`'s class
    # docstring for why these four remain the honest picture for a row
    # that has never been through a *partial* scientific/regulatory
    # merge. `source`/`verificationStatus` are always populated by a
    # real `Ingredient` row; `retrievedAt`/`lastVerifiedAt` are
    # epoch-millisecond timestamps (matching this API's existing
    # `timestamp`/`scannedAt` convention) or `null` when not yet set.
    # Once a row HAS been through a partial merge, per-field gating
    # below (`efsaApprovalStatus`/`fdaApprovalStatus`/`adiMin...`/
    # `adiMax...`) resolves that specific field's own true source
    # instead -- see `field_provenance_json`/`resolve_field_source`.
    verification_status: IngredientVerificationStatus = IngredientVerificationStatus.VERIFIED
    source: IngredientSource | None = None
    source_record_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime | None = None
    last_verified_at: datetime | None = None
    confidence: float | None = None
    schema_version: int | None = None
    # Internal only -- never serialized (`exclude=True`). Backs the
    # per-field source resolution below (PR #13 review fix:
    # "scientific/regulatory provenance truthful") -- a partial merge
    # must never make an untouched field's approval/ADI gating look
    # like it came from whatever source most recently touched a
    # DIFFERENT field on this row. See
    # `app.services.ingredient_catalog.resolve_field_source`.
    field_provenance_json: str | None = Field(default=None, exclude=True, repr=False)

    # `None` -- not True, not False -- whenever this specific status is
    # genuinely unknown (always the case for an UNVERIFIED OCR/Gemini
    # observation; a curated/seeded row always states a real value).
    # See task requirement 2: `False` would silently claim "confirmed
    # gluten-/lactose-free" and `True` would silently claim "confirmed
    # vegan/vegetarian/halal/kosher" with zero evidence behind either.
    is_gluten: bool | None = None
    is_lactose: bool | None = None
    is_vegan: bool | None = None
    is_vegetarian: bool | None = None
    is_halal: bool | None = None
    is_kosher: bool | None = None

    bad_for_diabetes: bool
    bad_for_hypertension: bool
    bad_for_kidney_disease: bool
    bad_for_gout: bool
    bad_for_pregnancy: bool
    bad_for_children: bool
    bad_for_high_cholesterol: bool

    @field_serializer("retrieved_at", "last_verified_at")
    def _serialize_epoch_millis(self, value: datetime | None) -> int | None:
        if value is None:
            return None
        return int(_as_utc(value).timestamp() * 1000)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def needs_refresh(self) -> bool:
        """Task requirement 5: a VERIFIED record's data is not cached
        forever. `True` once `lastVerifiedAt` is older than the
        configured TTL -- the last known value above is still exactly
        what a client should show; this only flags it for a future
        refresh, it never withholds/blocks anything."""
        if self.verification_status != IngredientVerificationStatus.VERIFIED:
            return False
        if self.last_verified_at is None:
            return False
        age = datetime.now(timezone.utc) - _as_utc(self.last_verified_at)
        return age > timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_rationale(self) -> str | None:
        """Why `riskLevel` is what it is, reusing the ingredient's own
        curated evidence-level text -- never a fabricated explanation.
        `None` whenever there is no real assessment to explain."""
        if not self.risk_assessment_available:
            return None
        return self.evidence_level or None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def efsa_approval_status(self) -> ApprovalStatus:
        """Task requirement 4: gated on the row's own trustworthiness,
        not just its text -- `NO_INFORMATION` unless this row is
        `VERIFIED` and `CURATED_SEED`/`REGULATORY_LOOKUP`-sourced (see
        `ingredient_regulatory.derive_gated_approval_status`). A
        Gemini/OCR-sourced `efsaStatus` string can never surface here as
        a real approval, no matter what it says. Gated on `efsaStatus`'s
        OWN true source (PR #13 review fix), not blindly this row's
        record-level `source` -- see `resolve_field_source`: a partial
        merge that only ever touched some OTHER field must never make
        an untouched `efsaStatus` look regulatory-confirmed."""
        return derive_gated_approval_status(
            self.efsa_status,
            verification_status=self.verification_status,
            source=resolve_field_source(self.field_provenance_json, "efsa_status", fallback=self.source),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fda_approval_status(self) -> ApprovalStatus:
        """See `efsa_approval_status` above -- identical gating, on
        `fdaStatus`'s own true source."""
        return derive_gated_approval_status(
            self.fda_status,
            verification_status=self.verification_status,
            source=resolve_field_source(self.field_provenance_json, "fda_status", fallback=self.source),
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_min_mg_per_kg_bw_per_day(self) -> float | None:
        return derive_gated_adi_range_mg_per_kg_bw_per_day(
            self.acceptable_daily_intake,
            verification_status=self.verification_status,
            source=resolve_field_source(self.field_provenance_json, "acceptable_daily_intake", fallback=self.source),
        )[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_max_mg_per_kg_bw_per_day(self) -> float | None:
        return derive_gated_adi_range_mg_per_kg_bw_per_day(
            self.acceptable_daily_intake,
            verification_status=self.verification_status,
            source=resolve_field_source(self.field_provenance_json, "acceptable_daily_intake", fallback=self.source),
        )[1]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_source(self) -> str | None:
        """The verified citation backing the numeric ADI range, only
        ever populated alongside an actual parsed, GATED number (see
        `adi_min_mg_per_kg_bw_per_day`)."""
        min_value, _ = derive_gated_adi_range_mg_per_kg_bw_per_day(
            self.acceptable_daily_intake,
            verification_status=self.verification_status,
            source=resolve_field_source(self.field_provenance_json, "acceptable_daily_intake", fallback=self.source),
        )
        if min_value is None:
            return None
        return self.references or None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ins_number_verified(self) -> bool:
        """Always `False` today (task: review of INS derivation
        authoritativeness). `insNumber` -- for a curated seed row
        exactly the same as for an OCR/Gemini row -- is always a
        MECHANICAL derivation from `eNumber` (see
        `ingredient_catalog.derive_ins_number_from_e_number`'s own
        docstring: true for MOST, not all, shared food additives), never
        independently confirmed against the Codex Alimentarius INS
        register itself. This field exists so a client can tell the
        difference between a truly independently-verified identifier and
        a mechanically-derived one, rather than `insNumber`'s mere
        presence implying a confidence it doesn't have -- reserved for a
        future real INS-register lookup to flip to `True`."""
        return False


class IngredientCreate(ORMModel):
    """Used by the seed/admin loader to insert scientific records."""

    id: str
    common_name: str
    scientific_name: str = ""
    e_number: str | None = None
    ins_number: str | None = None
    cas_number: str | None = None
    category: str = ""
    description: str = ""
    purpose_in_food: str = ""
    health_concerns: str = ""
    evidence_level: str = ""
    countries_restricted_or_banned: str = ""
    efsa_status: str = ""
    fda_status: str = ""
    who_iarc_classification: str | None = None
    acceptable_daily_intake: str = ""
    side_effects: str = ""
    allergens: str = ""
    references: str = ""
    risk_level: RiskLevel = RiskLevel.SAFE
    risk_assessment_available: bool = True
    verification_status: IngredientVerificationStatus = IngredientVerificationStatus.VERIFIED
    source: IngredientSource = IngredientSource.CURATED_SEED
    source_record_id: str | None = None
    source_url: str | None = None
    confidence: float = 1.0
    schema_version: int = 1

    is_gluten: bool = False
    is_lactose: bool = False
    is_vegan: bool = True
    is_vegetarian: bool = True
    is_halal: bool = True
    is_kosher: bool = True

    bad_for_diabetes: bool = False
    bad_for_hypertension: bool = False
    bad_for_kidney_disease: bool = False
    bad_for_gout: bool = False
    bad_for_pregnancy: bool = False
    bad_for_children: bool = False
    bad_for_high_cholesterol: bool = False
