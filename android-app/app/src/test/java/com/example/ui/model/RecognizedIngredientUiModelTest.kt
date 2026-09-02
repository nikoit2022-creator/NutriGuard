package com.example.ui.model

import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.ui.components.categorizeIngredientResults
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class RecognizedIngredientUiModelTest {
    @Test
    fun scoreBoundaries_mapToExpectedRatings() {
        assertEquals(IngredientSafetyRating.HIGH_CONCERN, ratingForSafetyScore(0))
        assertEquals(IngredientSafetyRating.HIGH_CONCERN, ratingForSafetyScore(29))
        assertEquals(IngredientSafetyRating.POTENTIAL_CONCERN, ratingForSafetyScore(30))
        assertEquals(IngredientSafetyRating.POTENTIAL_CONCERN, ratingForSafetyScore(54))
        assertEquals(IngredientSafetyRating.MODERATE, ratingForSafetyScore(55))
        assertEquals(IngredientSafetyRating.MODERATE, ratingForSafetyScore(79))
        assertEquals(IngredientSafetyRating.LOW_CONCERN, ratingForSafetyScore(80))
        assertEquals(IngredientSafetyRating.LOW_CONCERN, ratingForSafetyScore(100))
    }

    @Test
    fun missingScore_staysNullAndDoesNotFabricateNumber() {
        val model = ingredient().toRecognizedIngredientUiModel()

        assertNull(model.safetyScore)
        assertEquals(IngredientSafetyRating.LOW_CONCERN, model.rating)
    }

    @Test
    fun syntheticSafeIngredient_isLimitedData() {
        val model = ingredient(
            id = "synth_water",
            commonName = "Synth_water",
            description = "Ingredient extracted via OCR label scan.",
            purpose = "Food component / formulation ingredient.",
            riskLevel = RiskLevel.SAFE
        ).toRecognizedIngredientUiModel()

        assertEquals("Water", model.displayName)
        assertEquals(IngredientSafetyRating.LIMITED_DATA, model.rating)
        assertNull(model.safetyScore)
    }

    @Test
    fun generatedIngredientId_isLimitedDataEvenWithOptimisticDtoDefaults() {
        val model = ingredient(
            id = "ING_3800123456789_0",
            commonName = "Фруктоза",
            description = "",
            purpose = "",
            evidence = "Scientific Studies",
            riskLevel = RiskLevel.SAFE
        ).toRecognizedIngredientUiModel()

        assertEquals("Фруктоза", model.displayName)
        assertEquals(IngredientSafetyRating.LIMITED_DATA, model.rating)
    }

    @Test
    fun placeholderMetadata_isSuppressed() {
        val model = ingredient(
            eNumber = "null",
            evidence = "N/A",
            allergens = "None"
        ).toRecognizedIngredientUiModel()

        assertNull(model.eNumber)
        assertNull(model.evidenceLevel)
        assertNull(model.allergens)
    }

    @Test
    fun invalidInjectedScore_isIgnored() {
        val model = ingredient().toRecognizedIngredientUiModel(safetyScore = 101)

        assertNull(model.safetyScore)
        assertEquals(IngredientSafetyRating.LOW_CONCERN, model.rating)
    }

    @Test
    fun categorizedResults_showMostImportantGroupsFirst() {
        val sections = categorizeIngredientResults(
            listOf(
                ingredient(id = "water", commonName = "Water").toRecognizedIngredientUiModel(),
                ingredient(id = "colour", commonName = "Colour", riskLevel = RiskLevel.HIGH_CONCERN)
                    .toRecognizedIngredientUiModel(),
                ingredient(id = "synth_flavour", commonName = "Synth_flavour")
                    .toRecognizedIngredientUiModel(),
                ingredient(id = "salt", commonName = "Salt", riskLevel = RiskLevel.MODERATE)
                    .toRecognizedIngredientUiModel()
            )
        )

        assertEquals(
            listOf("High concern", "Use in moderation", "Low concern", "Limited data"),
            sections.map { it.title }
        )
        assertEquals("Colour", sections.first().models.single().displayName)
        assertEquals("Flavour", sections.last().models.single().displayName)
    }

    private fun ingredient(
        id: String = "citric_acid",
        commonName: String = "Citric acid",
        description: String = "A naturally occurring food acid.",
        purpose: String = "Acidity regulator",
        evidence: String = "Strong scientific consensus",
        eNumber: String? = "E330",
        allergens: String = "None",
        riskLevel: RiskLevel = RiskLevel.SAFE
    ) = IngredientEntity(
        id = id,
        commonName = commonName,
        scientificName = "",
        eNumber = eNumber,
        category = "Acidity regulator",
        description = description,
        purposeInFood = purpose,
        healthConcerns = "",
        evidenceLevel = evidence,
        countriesRestrictedOrBanned = "",
        efsaStatus = "",
        fdaStatus = "",
        acceptableDailyIntake = "",
        sideEffects = "",
        allergens = allergens,
        references = "EFSA",
        riskLevel = riskLevel
    )
}
