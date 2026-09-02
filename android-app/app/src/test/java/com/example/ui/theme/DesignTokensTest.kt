package com.example.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Covers [parseIarcGroup] -- the pure (Compose-free) classifier
 * [getWhoIarcUiColor] is built on. Review requirement: WHO/IARC values
 * must not all be colored red; Group 1/2A/2B are hazard-tiered, Group 3
 * ("not classifiable") is neutral, and an unparseable/unrecognized
 * string must never be assumed hazardous.
 */
class DesignTokensTest {

    @Test
    fun `real seed-data classification strings parse to the correct group`() {
        // Exact strings currently used in InitialScientificData.kt.
        assertEquals(IarcGroup.GROUP_2B, parseIarcGroup("Group 2B - Possibly Carcinogenic"))
        assertEquals(IarcGroup.GROUP_2B, parseIarcGroup("Group 2B - Possibly Carcinogenic (inhalation)"))
        assertEquals(IarcGroup.GROUP_2B, parseIarcGroup("Group 2B - Possibly Carcinogenic to Humans"))
        assertEquals(
            IarcGroup.GROUP_1,
            parseIarcGroup("Group 1 - Carcinogenic to humans (Ingested Nitrate/Nitrite under conditions leading to nitrosation)")
        )
    }

    @Test
    fun `group 2A is never mis-tiered as group 1 or 2B`() {
        assertEquals(IarcGroup.GROUP_2A, parseIarcGroup("Group 2A - Probably Carcinogenic to Humans"))
        assertEquals(IarcGroup.GROUP_2A, parseIarcGroup("group 2a"))
    }

    @Test
    fun `group 3 (not classifiable) parses distinctly from a hazard group`() {
        assertEquals(IarcGroup.GROUP_3, parseIarcGroup("Group 3 - Not Classifiable as to Carcinogenicity"))
    }

    @Test
    fun `case and spacing variations still parse correctly`() {
        assertEquals(IarcGroup.GROUP_1, parseIarcGroup("group1"))
        assertEquals(IarcGroup.GROUP_2B, parseIarcGroup("GROUP  2B"))
    }

    @Test
    fun `an unrecognized or empty classification never defaults to a hazard group`() {
        assertEquals(IarcGroup.UNKNOWN, parseIarcGroup(""))
        assertEquals(IarcGroup.UNKNOWN, parseIarcGroup("Not evaluated"))
        assertEquals(IarcGroup.UNKNOWN, parseIarcGroup("Group 4 - Probably Not Carcinogenic"))
        assertEquals(IarcGroup.UNKNOWN, parseIarcGroup("gibberish-123"))
    }
}
