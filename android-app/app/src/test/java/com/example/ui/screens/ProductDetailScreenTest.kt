package com.example.ui.screens

import com.example.ui.viewmodel.BarcodeLookupUiState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProductDetailScreenTest {
    @Test
    fun `label enrichment button is offered only for real GTIN-like product barcodes`() {
        assertTrue(isEnrichableProductBarcode("8606107983981"))
        assertTrue(isEnrichableProductBarcode(" 12345670 "))
        assertFalse(isEnrichableProductBarcode("img_123"))
        assertFalse(isEnrichableProductBarcode("ocr_label"))
        assertFalse(isEnrichableProductBarcode("12345"))
    }

    @Test
    fun `partial enrichment explains when nutrition table is still needed`() {
        val state = BarcodeLookupUiState.LabelScanRequired(
            reason = "Nutrition is incomplete",
            suggestedAction = null,
            discoveredIdentity = null,
            analysisComplete = false,
            healthScoreAvailable = false,
            healthScore = null,
            nutritionScanRequired = true,
            ingredientsScanRequired = false,
            ingredients = emptyList()
        )

        assertEquals(
            "Ingredients were saved to this product. Scan the nutrition table to complete it.",
            productEnrichmentStatusText(state, isForThisProduct = true)
        )
    }

    @Test
    fun `unrelated lookup state does not leak into product enrichment guidance`() {
        val state = BarcodeLookupUiState.Failed("Server unavailable", "other-barcode")

        assertEquals(
            "Photograph the ingredient list or nutrition table. You can repeat this for another side of the package.",
            productEnrichmentStatusText(state, isForThisProduct = false)
        )
    }
}
