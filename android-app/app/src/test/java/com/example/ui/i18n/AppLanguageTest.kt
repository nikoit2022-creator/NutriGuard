package com.example.ui.i18n

import org.junit.Assert.assertEquals
import org.junit.Test

class AppLanguageTest {
    @Test
    fun englishLeavesUiTextUnchanged() {
        assertEquals("Health Score", localizeUiText("Health Score", AppLanguage.ENGLISH))
    }

    @Test
    fun bulgarianTranslatesFixedUiText() {
        assertEquals("Здравна оценка", localizeUiText("Health Score", AppLanguage.BULGARIAN))
        assertEquals("Съставки (12)", localizeUiText("Ingredients (12)", AppLanguage.BULGARIAN))
    }

    @Test
    fun bulgarianTranslatesDynamicFactorAndBadgeText() {
        assertEquals(
            "Влияние върху здравната оценка: −10 точки",
            localizeUiText("Health Score impact: −10 points", AppLanguage.BULGARIAN)
        )
        assertEquals("✓ Веган", localizeUiText("✓ Vegan", AppLanguage.BULGARIAN))
        assertEquals("6.8 g на 100 g", localizeUiText("6.8 g per 100 g", AppLanguage.BULGARIAN))
    }

    @Test
    fun unknownProductAndScientificTextIsPreserved() {
        val original = "Chocolate negro. Cacao 95% minimo"
        assertEquals(original, localizeUiText(original, AppLanguage.BULGARIAN))
    }

    @Test
    fun storedLanguageFallsBackSafely() {
        assertEquals(AppLanguage.BULGARIAN, AppLanguage.fromCode("bg"))
        assertEquals(AppLanguage.ENGLISH, AppLanguage.fromCode("unsupported"))
    }
}
