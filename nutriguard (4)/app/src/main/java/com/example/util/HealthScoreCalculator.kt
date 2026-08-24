package com.example.util

import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel

data class HealthScoreBreakdown(
    val totalScore: Int, // 0 to 100
    val ingredientQualityScore: Int, // 0 to 100
    val additivesDeduction: Int,
    val sugarDeduction: Int,
    val sodiumDeduction: Int,
    val saturatedFatDeduction: Int,
    val artificialSweetenerDeduction: Int,
    val preservativeDeduction: Int,
    val novaDeduction: Int,
    val novaGroup: Int,
    val novaDescription: String
)

object HealthScoreCalculator {

    fun calculate(
        ingredients: List<IngredientEntity>,
        sugarGrams: Double,
        sodiumMg: Double,
        saturatedFatGrams: Double,
        hasArtificialSweeteners: Boolean,
        hasPreservatives: Boolean,
        novaGroup: Int
    ): HealthScoreBreakdown {
        var score = 100

        // 1. Ingredient Quality Deductions
        var highRiskCount = 0
        var potentialConcernCount = 0
        var moderateCount = 0

        ingredients.forEach { ing ->
            when (ing.riskLevel) {
                RiskLevel.HIGH_CONCERN -> highRiskCount++
                RiskLevel.POTENTIAL_CONCERN -> potentialConcernCount++
                RiskLevel.MODERATE -> moderateCount++
                RiskLevel.SAFE -> {}
            }
        }

        val additivesDeduction = (highRiskCount * 20) + (potentialConcernCount * 12) + (moderateCount * 5)
        score -= additivesDeduction

        // 2. Sugar Deduction (Per 100g)
        val sugarDeduction = when {
            sugarGrams > 20.0 -> 25
            sugarGrams > 12.0 -> 18
            sugarGrams > 5.0 -> 10
            sugarGrams > 2.0 -> 4
            else -> 0
        }
        score -= sugarDeduction

        // 3. Sodium Deduction (Per 100g in mg)
        val sodiumDeduction = when {
            sodiumMg > 900.0 -> 25
            sodiumMg > 600.0 -> 18
            sodiumMg > 300.0 -> 10
            sodiumMg > 120.0 -> 5
            else -> 0
        }
        score -= sodiumDeduction

        // 4. Saturated Fat Deduction (Per 100g)
        val satFatDeduction = when {
            saturatedFatGrams > 8.0 -> 20
            saturatedFatGrams > 5.0 -> 12
            saturatedFatGrams > 2.5 -> 6
            else -> 0
        }
        score -= satFatDeduction

        // 5. Artificial Sweeteners Deduction
        val sweetenerDeduction = if (hasArtificialSweeteners) 12 else 0
        score -= sweetenerDeduction

        // 6. Preservatives Deduction
        val preservativeDeduction = if (hasPreservatives) 10 else 0
        score -= preservativeDeduction

        // 7. NOVA Classification Penalty
        val (novaDeduction, novaDesc) = when (novaGroup) {
            4 -> 20 to "Ultra-Processed Food (NOVA 4)"
            3 -> 10 to "Processed Food (NOVA 3)"
            2 -> 5 to "Processed Culinary Ingredient (NOVA 2)"
            else -> 0 to "Unprocessed / Minimally Processed (NOVA 1)"
        }
        score -= novaDeduction

        val finalScore = score.coerceIn(0, 100)
        val ingredientQualityScore = (100 - (additivesDeduction + sweetenerDeduction + preservativeDeduction)).coerceIn(0, 100)

        return HealthScoreBreakdown(
            totalScore = finalScore,
            ingredientQualityScore = ingredientQualityScore,
            additivesDeduction = additivesDeduction,
            sugarDeduction = sugarDeduction,
            sodiumDeduction = sodiumDeduction,
            saturatedFatDeduction = satFatDeduction,
            artificialSweetenerDeduction = sweetenerDeduction,
            preservativeDeduction = preservativeDeduction,
            novaDeduction = novaDeduction,
            novaGroup = novaGroup,
            novaDescription = novaDesc
        )
    }
}
