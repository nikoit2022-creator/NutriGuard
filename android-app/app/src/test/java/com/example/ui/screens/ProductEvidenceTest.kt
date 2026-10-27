package com.example.ui.screens

import com.example.data.model.ProductEntity
import com.example.util.dietaryDisplayStatus
import com.example.util.namedAllergens
import org.junit.Assert.assertEquals
import org.junit.Test

class ProductEvidenceTest {
    private val label = "Wheat flour, sugar, skim milk powder, egg melange. May contain sesame seeds."

    @Test fun tracesAreSeparateFromIngredients() {
        assertEquals("sesame seeds", extractPossibleTraces(label))
        assertEquals("", extractPossibleTraces("Wheat flour, sesame seeds"))
    }

    @Test fun missingDietaryEvidenceIsNotShownAsFailure() {
        val product = ProductEntity(
            barcode = "4823077614675", productName = "Biscuits", brand = "Roshen",
            category = "Biscuit", rawIngredientText = label, ingredientIds = "",
            healthScore = 38, novaGroup = 4, sugarGrams = 20.7, sodiumMg = 284.0,
            saturatedFatGrams = 8.0, hasArtificialSweeteners = false,
            hasPreservatives = false, isGlutenFree = false, isLactoseFree = false,
            isVegan = false, isVegetarian = false, isHalal = false,
            isKosher = false, allergensDetected = "Gluten, Milk"
        )
        assertEquals("no", product.dietaryDisplayStatus("gluten"))
        assertEquals("no", product.dietaryDisplayStatus("vegan"))
        assertEquals("unknown", product.dietaryDisplayStatus("vegetarian"))
        assertEquals("unknown", product.dietaryDisplayStatus("halal"))
        assertEquals("unknown", product.dietaryDisplayStatus("kosher"))
        assertEquals("yes", product.copy(dietaryStatusesJson = "{\"halal\":\"yes\"}").dietaryDisplayStatus("halal"))
        assertEquals(true, "Eggs" in product.namedAllergens())
        assertEquals(false, "Sesame" in product.namedAllergens())
    }
}
