from app.models.enums import RiskLevel
from app.schemas.common import ORMModel


class IngredientOut(ORMModel):
    """Mirrors com.example.data.model.IngredientEntity exactly (API Contract 5.4)."""

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
