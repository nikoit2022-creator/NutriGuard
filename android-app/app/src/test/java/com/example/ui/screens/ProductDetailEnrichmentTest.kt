package com.example.ui.screens

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProductDetailEnrichmentTest {
    @Test
    fun `offers enrichment for a real barcode with only one ingredient`() {
        assertTrue(
            shouldOfferLabelEnrichment(
                barcode = "7613034626844",
                hasVerifiedIngredients = true,
                ingredientCount = 1,
                rawIngredientText = "sugar"
            )
        )
    }

    @Test
    fun `offers enrichment when barcode ingredients are not verified`() {
        assertTrue(
            shouldOfferLabelEnrichment(
                barcode = "7613034626844",
                hasVerifiedIngredients = false,
                ingredientCount = 4,
                rawIngredientText = "ingredients"
            )
        )
    }

    @Test
    fun `does not attach synthetic product ids as barcodes`() {
        assertFalse(
            shouldOfferLabelEnrichment(
                barcode = "img_123456",
                hasVerifiedIngredients = false,
                ingredientCount = 0,
                rawIngredientText = ""
            )
        )
    }

    @Test
    fun `hides enrichment for a complete product`() {
        assertFalse(
            shouldOfferLabelEnrichment(
                barcode = "7613034626844",
                hasVerifiedIngredients = true,
                ingredientCount = 6,
                rawIngredientText = "wheat flour, sugar, salt, cocoa, oil, vitamins"
            )
        )
    }
}
