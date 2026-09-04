from pydantic import computed_field

from app.models.enums import ApprovalStatus, RiskLevel
from app.schemas.common import ORMModel
from app.services.ingredient_regulatory import (
    derive_adi_range_mg_per_kg_bw_per_day,
    derive_approval_status,
)


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

    is_gluten: bool
    is_lactose: bool
    is_vegan: bool
    is_vegetarian: bool
    is_halal: bool
    is_kosher: bool

    bad_for_diabetes: bool
    bad_for_hypertension: bool
    bad_for_kidney_disease: bool
    bad_for_gout: bool
    bad_for_pregnancy: bool
    bad_for_children: bool
    bad_for_high_cholesterol: bool

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
        return derive_approval_status(self.efsa_status)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fda_approval_status(self) -> ApprovalStatus:
        return derive_approval_status(self.fda_status)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_min_mg_per_kg_bw_per_day(self) -> float | None:
        return derive_adi_range_mg_per_kg_bw_per_day(self.acceptable_daily_intake)[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_max_mg_per_kg_bw_per_day(self) -> float | None:
        return derive_adi_range_mg_per_kg_bw_per_day(self.acceptable_daily_intake)[1]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def adi_source(self) -> str | None:
        """The verified citation backing the numeric ADI range, only
        ever populated alongside an actual parsed number."""
        min_value, _ = derive_adi_range_mg_per_kg_bw_per_day(self.acceptable_daily_intake)
        if min_value is None:
            return None
        return self.references or None


class IngredientCreate(ORMModel):
    """Used by the seed/admin loader to insert scientific records."""

    id: str
    common_name: str
    scientific_name: str = ""
    e_number: str | None = None
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
