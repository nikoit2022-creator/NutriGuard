"""
Deterministic port of `com.example.util.HealthScoreCalculator`.

Every threshold and weight below is copied verbatim from the Kotlin
source (see API Contract section 7.1). Do NOT change values here without
updating the contract and the Kotlin client in lockstep — the whole point
of this module is behavioral parity with the existing app.
"""
from dataclasses import dataclass

from app.models.enums import RiskLevel


@dataclass(frozen=True)
class HealthScoreBreakdown:
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


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def calculate(
    *,
    ingredient_risk_levels: list[RiskLevel],
    sugar_grams: float,
    sodium_mg: float,
    saturated_fat_grams: float,
    has_artificial_sweeteners: bool,
    has_preservatives: bool,
    nova_group: int,
) -> HealthScoreBreakdown:
    score = 100

    # 1. Ingredient quality deductions
    high_risk_count = sum(1 for r in ingredient_risk_levels if r == RiskLevel.HIGH_CONCERN)
    potential_concern_count = sum(1 for r in ingredient_risk_levels if r == RiskLevel.POTENTIAL_CONCERN)
    moderate_count = sum(1 for r in ingredient_risk_levels if r == RiskLevel.MODERATE)

    additives_deduction = (high_risk_count * 20) + (potential_concern_count * 12) + (moderate_count * 5)
    score -= additives_deduction

    # 2. Sugar deduction (per 100g)
    if sugar_grams > 20.0:
        sugar_deduction = 25
    elif sugar_grams > 12.0:
        sugar_deduction = 18
    elif sugar_grams > 5.0:
        sugar_deduction = 10
    elif sugar_grams > 2.0:
        sugar_deduction = 4
    else:
        sugar_deduction = 0
    score -= sugar_deduction

    # 3. Sodium deduction (per 100g, mg)
    if sodium_mg > 900.0:
        sodium_deduction = 25
    elif sodium_mg > 600.0:
        sodium_deduction = 18
    elif sodium_mg > 300.0:
        sodium_deduction = 10
    elif sodium_mg > 120.0:
        sodium_deduction = 5
    else:
        sodium_deduction = 0
    score -= sodium_deduction

    # 4. Saturated fat deduction (per 100g)
    if saturated_fat_grams > 8.0:
        sat_fat_deduction = 20
    elif saturated_fat_grams > 5.0:
        sat_fat_deduction = 12
    elif saturated_fat_grams > 2.5:
        sat_fat_deduction = 6
    else:
        sat_fat_deduction = 0
    score -= sat_fat_deduction

    # 5. Artificial sweeteners
    sweetener_deduction = 12 if has_artificial_sweeteners else 0
    score -= sweetener_deduction

    # 6. Preservatives
    preservative_deduction = 10 if has_preservatives else 0
    score -= preservative_deduction

    # 7. NOVA classification penalty
    if nova_group == 4:
        nova_deduction, nova_desc = 20, "Ultra-Processed Food (NOVA 4)"
    elif nova_group == 3:
        nova_deduction, nova_desc = 10, "Processed Food (NOVA 3)"
    elif nova_group == 2:
        nova_deduction, nova_desc = 5, "Processed Culinary Ingredient (NOVA 2)"
    else:
        nova_deduction, nova_desc = 0, "Unprocessed / Minimally Processed (NOVA 1)"
    score -= nova_deduction

    final_score = _clamp(score, 0, 100)
    ingredient_quality_score = _clamp(
        100 - (additives_deduction + sweetener_deduction + preservative_deduction), 0, 100
    )

    return HealthScoreBreakdown(
        total_score=final_score,
        ingredient_quality_score=ingredient_quality_score,
        additives_deduction=additives_deduction,
        sugar_deduction=sugar_deduction,
        sodium_deduction=sodium_deduction,
        saturated_fat_deduction=sat_fat_deduction,
        artificial_sweetener_deduction=sweetener_deduction,
        preservative_deduction=preservative_deduction,
        nova_deduction=nova_deduction,
        nova_group=nova_group,
        nova_description=nova_desc,
    )
