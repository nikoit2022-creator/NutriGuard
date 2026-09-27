package com.example.ui.i18n

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.Column
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.example.ui.theme.NutriGuardScannerTheme
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class LanguageTopBarTest {
    @get:Rule val compose = createComposeRule()

    @Test fun languageChoicesStayBelowSystemInsetAndSwitchBothWays() {
        compose.setContent {
            var language by remember { mutableStateOf(AppLanguage.ENGLISH) }
            CompositionLocalProvider(LocalAppLanguage provides language) {
                NutriGuardScannerTheme {
                    Column {
                        LanguageTopBar(language, { language = it }, WindowInsets(0, 100, 0, 0))
                        LocalizedText("Ingredients recognized")
                    }
                }
            }
        }
        val english = compose.onNodeWithText("EN")
        english.assertIsDisplayed()
        assertTrue(english.fetchSemanticsNode().boundsInRoot.top >= 100f)
        compose.onNodeWithText("BG").assertIsDisplayed().performClick()
        compose.onNodeWithText("Разпознати съставки").assertIsDisplayed()
        english.performClick()
        compose.onNodeWithText("Ingredients recognized").assertIsDisplayed()
    }
}
