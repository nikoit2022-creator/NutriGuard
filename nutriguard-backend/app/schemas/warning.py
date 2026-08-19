from app.models.enums import WarningSeverity
from app.schemas.common import ORMModel


class HealthWarningOut(ORMModel):
    """Mirrors com.example.util.HealthWarning (API Contract 5.8)."""

    title: str
    description: str
    condition: str
    trigger_factor: str
    severity: WarningSeverity


class HealthScoreBreakdownOut(ORMModel):
    """Mirrors com.example.util.HealthScoreBreakdown (API Contract 5.10)."""

    total_score: int
    ingredient_quality_score: int
    additives_deduction: int
    sugar_deduction: int
    sodium_deduction: int
    saturated_fat_deduction: int
    artificial_sweetener_deduction: int
    preservative_deduction: int
    nova_deduction: int
    nova_group: int
    nova_description: str
