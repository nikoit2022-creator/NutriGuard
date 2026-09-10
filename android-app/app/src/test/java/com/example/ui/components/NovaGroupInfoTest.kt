package com.example.ui.components

import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NovaGroupInfoTest {
    @Test
    fun novaGroups_haveCorrectTitlesAndUnknownGroupIsRejected() {
        assertEquals("Unprocessed or minimally processed", novaGroupUiInfo(1)?.title)
        assertEquals("Processed culinary ingredients", novaGroupUiInfo(2)?.title)
        assertEquals("Processed foods", novaGroupUiInfo(3)?.title)
        assertEquals("Ultra-processed foods", novaGroupUiInfo(4)?.title)
        assertNull(novaGroupUiInfo(0))
    }

    @Test
    fun ingredientWithoutSubstantiveDataIsNotPresentedAsClickable() {
        assertFalse(hasUsefulIngredientDetails(ingredient()))
    }

    @Test
    fun ingredientWithUsefulHealthOrScientificDataIsClickable() {
        assertTrue(hasUsefulIngredientDetails(ingredient(description = "A phospholipid mixture.")))
        assertTrue(hasUsefulIngredientDetails(ingredient(sideEffects = "May cause mild GI effects.")))
        assertTrue(hasUsefulIngredientDetails(ingredient(allergens = "Soy")))
    }

    private fun ingredient(
        description: String = "",
        sideEffects: String = "",
        allergens: String = ""
    ) = IngredientEntity(
        id = "test",
        commonName = "Test ingredient",
        scientificName = "",
        category = "",
        description = description,
        purposeInFood = "",
        healthConcerns = "",
        evidenceLevel = "",
        countriesRestrictedOrBanned = "",
        efsaStatus = "",
        fdaStatus = "",
        acceptableDailyIntake = "",
        sideEffects = sideEffects,
        allergens = allergens,
        references = "",
        riskLevel = RiskLevel.SAFE,
        riskAssessmentAvailable = false
    )
}
