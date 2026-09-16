package com.example.ui.components

import org.junit.Assert.assertEquals
import org.junit.Test

class HealthFactorInfoTest {
    @Test
    fun `sugar deductions match health score thresholds`() {
        assertEquals(0, sugarScoreDeduction(2.0))
        assertEquals(4, sugarScoreDeduction(2.1))
        assertEquals(10, sugarScoreDeduction(6.8))
        assertEquals(18, sugarScoreDeduction(12.1))
        assertEquals(25, sugarScoreDeduction(20.1))
    }

    @Test
    fun `sodium deductions match health score thresholds`() {
        assertEquals(0, sodiumScoreDeduction(120.0))
        assertEquals(5, sodiumScoreDeduction(120.1))
        assertEquals(10, sodiumScoreDeduction(300.1))
        assertEquals(18, sodiumScoreDeduction(600.1))
        assertEquals(25, sodiumScoreDeduction(1120.0))
    }

    @Test
    fun `saturated fat deductions match health score thresholds`() {
        assertEquals(0, saturatedFatScoreDeduction(2.5))
        assertEquals(6, saturatedFatScoreDeduction(2.6))
        assertEquals(12, saturatedFatScoreDeduction(5.1))
        assertEquals(20, saturatedFatScoreDeduction(8.1))
    }
}
