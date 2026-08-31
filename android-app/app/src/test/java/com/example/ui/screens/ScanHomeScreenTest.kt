package com.example.ui.screens

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

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
}
