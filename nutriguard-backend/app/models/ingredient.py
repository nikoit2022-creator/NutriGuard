from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import RiskLevel


class Ingredient(Base):
    """
    Mirrors com.example.data.model.IngredientEntity and the
    `scientific_ingredients` table sketched in ArchitectureAdminScreen.kt.
    """

    __tablename__ = "ingredients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    common_name: Mapped[str] = mapped_column("common_name", String(255), nullable=False, index=True)
    scientific_name: Mapped[str] = mapped_column("scientific_name", String(255), nullable=False, default="")
    e_number: Mapped[str | None] = mapped_column("e_number", String(16), unique=True, nullable=True, index=True)
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
    # True for every curated/seeded row in this table -- an OCR-only
    # "synthetic" ingredient (see app.services.ocr_normalizer) is never
    # persisted here, it is reconstructed on the fly with this flag set
    # to False, so a genuinely unassessed ingredient can never be
    # confused with a real risk assessment on the wire (see
    # `IngredientOut.risk_assessment_available` / `risk_rationale`).
    risk_assessment_available: Mapped[bool] = mapped_column(
        "risk_assessment_available", Boolean, nullable=False, default=True
    )

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
