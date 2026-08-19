from app.models.enums import RiskLevel
from app.services.health_score import calculate


def test_perfect_clean_product_scores_100():
    result = calculate(
        ingredient_risk_levels=[],
        sugar_grams=1.0,
        sodium_mg=50.0,
        saturated_fat_grams=0.5,
        has_artificial_sweeteners=False,
        has_preservatives=False,
        nova_group=1,
    )
    assert result.total_score == 100
    assert result.ingredient_quality_score == 100
    assert result.nova_description == "Unprocessed / Minimally Processed (NOVA 1)"


def test_worst_case_product_clamps_to_zero():
    result = calculate(
        ingredient_risk_levels=[RiskLevel.HIGH_CONCERN, RiskLevel.HIGH_CONCERN],
        sugar_grams=25.0,
        sodium_mg=1000.0,
        saturated_fat_grams=10.0,
        has_artificial_sweeteners=True,
        has_preservatives=True,
        nova_group=4,
    )
    assert result.total_score == 0  # clamped, even though raw deductions exceed 100
    assert result.ingredient_quality_score == 38
    assert result.additives_deduction == 40  # 2 * 20
    assert result.sugar_deduction == 25
    assert result.sodium_deduction == 25
    assert result.saturated_fat_deduction == 20
    assert result.artificial_sweetener_deduction == 12
    assert result.preservative_deduction == 10
    assert result.nova_deduction == 20
    assert result.nova_description == "Ultra-Processed Food (NOVA 4)"


def test_thresholds_are_strictly_greater_than_not_greater_or_equal():
    """Values exactly AT a threshold must NOT trigger that threshold's
    deduction (API Contract 7.1 uses strict `>` comparisons)."""
    result = calculate(
        ingredient_risk_levels=[],
        sugar_grams=20.0,  # == 20.0, must fall into the next-lower bracket
        sodium_mg=900.0,  # == 900.0
        saturated_fat_grams=8.0,  # == 8.0
        has_artificial_sweeteners=False,
        has_preservatives=False,
        nova_group=3,
    )
    assert result.sugar_deduction == 18  # not 25
    assert result.sodium_deduction == 18  # not 25
    assert result.saturated_fat_deduction == 12  # not 20
    assert result.total_score == 42


def test_mixed_ingredient_risk_and_nova_2():
    result = calculate(
        ingredient_risk_levels=[RiskLevel.MODERATE, RiskLevel.POTENTIAL_CONCERN, RiskLevel.SAFE],
        sugar_grams=6.0,
        sodium_mg=350.0,
        saturated_fat_grams=3.0,
        has_artificial_sweeteners=False,
        has_preservatives=True,
        nova_group=2,
    )
    # additives = 1*moderate(5) + 1*potential_concern(12) + 0 for SAFE = 17
    assert result.additives_deduction == 17
    assert result.sugar_deduction == 10
    assert result.sodium_deduction == 10
    assert result.saturated_fat_deduction == 6
    assert result.preservative_deduction == 10
    assert result.nova_deduction == 5
    assert result.total_score == 42
    assert result.ingredient_quality_score == 73  # 100 - (17 + 0 + 10)


def test_score_never_goes_below_zero_or_above_hundred():
    worst = calculate(
        ingredient_risk_levels=[RiskLevel.HIGH_CONCERN] * 10,
        sugar_grams=999,
        sodium_mg=9999,
        saturated_fat_grams=999,
        has_artificial_sweeteners=True,
        has_preservatives=True,
        nova_group=4,
    )
    assert 0 <= worst.total_score <= 100
    assert 0 <= worst.ingredient_quality_score <= 100
