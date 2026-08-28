package com.example.service.ocr

import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel

data class NormalizedIngredientResult(
    val matchedIngredients: List<IngredientEntity>,
    val unknownIngredients: List<String>,
    val rawTokens: List<String>
)

object OcrNormalizer {

    fun normalizeAndExtractTokens(rawText: String): List<String> {
        // Clean up common OCR artifacts
        val cleaned = rawText
            .replace(Regex("\\[.*?\\]|\\(.*?%\\)"), "") // Remove bracket percentages
            .replace("\n", " ")
            .replace("Ingredients:", "", ignoreCase = true)
            .replace("CONTAINS:", "", ignoreCase = true)

        // Split by separators
        return cleaned.split(Regex("[,;.]"))
            .map { token ->
                token.trim()
                    .replace(Regex("^\\W+|\\W+$"), "") // Trim non-word characters
            }
            .filter { it.length > 1 }
    }

    fun matchAgainstDatabase(
        tokens: List<String>,
        dbIngredients: List<IngredientEntity>
    ): NormalizedIngredientResult {
        val matched = mutableListOf<IngredientEntity>()
        val unknown = mutableListOf<String>()

        tokens.forEach { token ->
            val lowerToken = token.lowercase()
            // Try matching E-number first
            val eMatch = Regex("e[- ]?(\\d{3,4}[a-z]?)", RegexOption.IGNORE_CASE).find(lowerToken)
            var found: IngredientEntity? = null

            if (eMatch != null) {
                val formattedE = "E" + eMatch.groupValues[1].uppercase()
                found = dbIngredients.find { it.eNumber?.equals(formattedE, ignoreCase = true) == true }
            }

            if (found == null) {
                // Try exact or partial name match
                found = dbIngredients.find {
                    lowerToken.contains(it.commonName.lowercase()) ||
                            it.commonName.lowercase().contains(lowerToken) ||
                            (it.scientificName.isNotBlank() && lowerToken.contains(it.scientificName.lowercase()))
                }
            }

            if (found != null) {
                if (!matched.contains(found)) {
                    matched.add(found)
                }
            } else {
                if (token.isNotBlank() && !unknown.contains(token)) {
                    unknown.add(token)
                }
            }
        }

        return NormalizedIngredientResult(
            matchedIngredients = matched,
            unknownIngredients = unknown,
            rawTokens = tokens
        )
    }

    fun createSyntheticIngredient(name: String): IngredientEntity {
        val lower = name.lowercase()
        val eMatch = Regex("e[- ]?(\\d{3,4}[a-z]?)", RegexOption.IGNORE_CASE).find(lower)
        val formattedE = eMatch?.let { "E" + it.groupValues[1].uppercase() }

        val isHighRisk = lower.contains("nitrit") || lower.contains("aspartam") || lower.contains("titanium") || lower.contains("benzoate") || lower.contains("dye") || lower.contains("red 40")
        val isModerate = lower.contains("sugar") || lower.contains("syrup") || lower.contains("msg") || lower.contains("glutamate") || lower.contains("fat") || lower.contains("oil")

        val risk = when {
            isHighRisk -> RiskLevel.HIGH_CONCERN
            isModerate -> RiskLevel.POTENTIAL_CONCERN
            else -> RiskLevel.SAFE
        }

        return IngredientEntity(
            id = "synth_" + name.lowercase().replace(" ", "_").replace(Regex("[^a-z0-9_]"), ""),
            commonName = name.replaceFirstChar { it.uppercase() },
            scientificName = formattedE ?: "Normalized Food Component",
            eNumber = formattedE,
            category = if (formattedE != null) "Food Additive ($formattedE)" else "Ingredient",
            description = "Ingredient extracted via OCR label scan.",
            purposeInFood = "Food component / formulation ingredient.",
            healthConcerns = if (risk == RiskLevel.HIGH_CONCERN) "Contains additives associated with health warnings." else "Standard ingredient.",
            evidenceLevel = "Extracted via OCR",
            countriesRestrictedOrBanned = "Subject to standard local food safety regulations.",
            efsaStatus = "Standard Food Additive/Ingredient",
            fdaStatus = "Recognized Ingredient",
            acceptableDailyIntake = "Standard dietary intake",
            sideEffects = "See individual sensitivity profile",
            allergens = if (lower.contains("milk") || lower.contains("whey") || lower.contains("soy") || lower.contains("wheat") || lower.contains("peanut")) "Potential Allergen" else "None",
            references = "NutriGuard OCR & Scientific Pipeline",
            riskLevel = risk,
            badForDiabetes = lower.contains("sugar") || lower.contains("syrup") || lower.contains("dextrose"),
            badForHypertension = lower.contains("sodium") || lower.contains("salt") || lower.contains("msg"),
            badForHighCholesterol = lower.contains("palm") || lower.contains("fat") || lower.contains("hydrogenated")
        )
    }
}
