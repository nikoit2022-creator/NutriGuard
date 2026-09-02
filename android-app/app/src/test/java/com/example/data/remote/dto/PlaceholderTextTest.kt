package com.example.data.remote.dto

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PlaceholderTextTest {

    @Test
    fun `blank and whitespace-only values become null`() {
        assertNull(null.cleanOrNull())
        assertNull("".cleanOrNull())
        assertNull("   ".cleanOrNull())
    }

    @Test
    fun `known literal placeholders become null regardless of case`() {
        for (placeholder in listOf("null", "NULL", "None", "NONE", "n/a", "N/A", "undefined", "UNDEFINED", "-", "unknown")) {
            assertNull("expected '$placeholder' to be filtered", placeholder.cleanOrNull())
        }
    }

    @Test
    fun `real text is trimmed and preserved`() {
        assertEquals("Fizzy Orange Soda", "  Fizzy Orange Soda  ".cleanOrNull())
        assertEquals("Acme", "Acme".cleanOrNull())
    }

    @Test
    fun `valid Bulgarian Cyrillic text is preserved`() {
        assertEquals("Кисело Мляко", "Кисело Мляко".cleanOrNull())
    }

    @Test
    fun `a word that merely contains a placeholder substring is preserved`() {
        // "Nonetheless Bakery" must not be treated as the placeholder "none".
        assertEquals("Nonetheless Bakery", "Nonetheless Bakery".cleanOrNull())
    }

    // --- Ingredient UI fixes (review requirement: hide E-Number/WHO-IARC
    // badges when there is no real value -- IngredientChip/
    // IngredientDetailBottomSheet both gate on `cleanOrNull() != null`,
    // this is the exact function/values they apply it to.) --------------

    @Test
    fun `a real E-number is preserved for display`() {
        assertEquals("E211", "E211".cleanOrNull())
        assertEquals("E951", " E951 ".cleanOrNull())
    }

    @Test
    fun `a missing or placeholder E-number is hidden, not shown as literal text`() {
        assertNull((null as String?).cleanOrNull())
        assertNull("".cleanOrNull())
        assertNull("null".cleanOrNull())
        assertNull("N/A".cleanOrNull())
    }

    @Test
    fun `a real WHO-IARC classification is preserved for display`() {
        assertEquals(
            "Group 2B - Possibly Carcinogenic",
            "Group 2B - Possibly Carcinogenic".cleanOrNull()
        )
    }

    @Test
    fun `a missing or placeholder WHO-IARC classification is hidden`() {
        assertNull((null as String?).cleanOrNull())
        assertNull("unknown".cleanOrNull())
        assertNull("-".cleanOrNull())
    }
}
