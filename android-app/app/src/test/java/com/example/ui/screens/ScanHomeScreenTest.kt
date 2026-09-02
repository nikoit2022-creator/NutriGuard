package com.example.ui.screens

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Note on test level: `ScanHomeScreen`'s Composables pull in
 * `GmsBarcodeScanning`/Play-Services-backed clients at composition time
 * (`remember { GmsBarcodeScanning.getClient(...) }`), which this
 * project has no Robolectric shadow/mocking setup for yet. Consistent
 * with this file's existing convention, the screen's UI-decision logic
 * (fixed button copy, missing-evidence messaging) is factored into
 * plain, Compose-free top-level functions/constants and tested
 * directly here, rather than mounting the full screen.
 */
class ScanHomeScreenTest {
    @Test
    fun normalizeScannedBarcode_trimsDetectedValue() {
        assertEquals("3800123456789", normalizeScannedBarcode(" 3800123456789 "))
    }

    @Test
    fun normalizeScannedBarcode_rejectsMissingOrBlankValue() {
        assertNull(normalizeScannedBarcode(null))
        assertNull(normalizeScannedBarcode(""))
        assertNull(normalizeScannedBarcode("   "))
    }

    // --- Fixed "Scan label for more information" action text ------------

    @Test
    fun scanLabelActionText_isTheExactRequiredCopy() {
        assertEquals("Scan label for more information", SCAN_LABEL_ACTION_TEXT)
    }

    // --- missingEvidenceMessages (review requirement: "Nutrition
    // information is still needed" / "Ingredient information is still
    // needed", and both together when the sequential-photo flow still
    // needs both groups) -----------------------------------------------

    @Test
    fun missingEvidenceMessages_nutritionOnly() {
        val messages = missingEvidenceMessages(nutritionScanRequired = true, ingredientsScanRequired = false)
        assertEquals(listOf("Nutrition information is still needed"), messages)
    }

    @Test
    fun missingEvidenceMessages_ingredientsOnly() {
        val messages = missingEvidenceMessages(nutritionScanRequired = false, ingredientsScanRequired = true)
        assertEquals(listOf("Ingredient information is still needed"), messages)
    }

    @Test
    fun missingEvidenceMessages_bothMissing_allowsSequentialPhotos() {
        val messages = missingEvidenceMessages(nutritionScanRequired = true, ingredientsScanRequired = true)
        assertEquals(2, messages.size)
        assertTrue(messages.contains("Nutrition information is still needed"))
        assertTrue(messages.contains("Ingredient information is still needed"))
    }

    @Test
    fun missingEvidenceMessages_neitherMissing_isEmpty() {
        assertTrue(missingEvidenceMessages(nutritionScanRequired = false, ingredientsScanRequired = false).isEmpty())
    }

    @Test
    fun missingEvidenceMessages_nullFlags_areTreatedAsNotMissing() {
        // null means "the backend didn't send this field" (the plain
        // not-found-anywhere 404 shape) -- must never be treated as
        // "true"/missing, which would show a message with nothing to
        // actually act on.
        assertTrue(missingEvidenceMessages(nutritionScanRequired = null, ingredientsScanRequired = null).isEmpty())
    }
}
