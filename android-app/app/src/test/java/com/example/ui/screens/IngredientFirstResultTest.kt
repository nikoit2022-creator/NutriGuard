package com.example.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import com.example.data.remote.dto.IngredientDto
import com.example.data.remote.dto.toEntities
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage
import com.example.ui.theme.NutriGuardScannerTheme
import com.example.ui.viewmodel.BarcodeLookupUiState
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class IngredientFirstResultTest {
    @get:Rule val compose = createComposeRule()

    private fun result(withIngredient: Boolean = true, label: Boolean = true) =
        BarcodeLookupUiState.LabelScanRequired(
            reason = "This product is incomplete", suggestedAction = null,
            discoveredIdentity = null, analysisComplete = false,
            healthScoreAvailable = false, healthScore = null,
            nutritionScanRequired = true, ingredientsScanRequired = true,
            ingredients = if (withIngredient) listOf(
                IngredientDto(id = "sugar", commonName = "Sugar")
            ).toEntities("test") else emptyList(),
            fromLabelSubmission = label
        )

    @Test fun ingredientsRemainClickableDespiteBothMissingFlagsAndNoScore() {
        var selected = ""
        var added = false
        compose.setContent {
            NutriGuardScannerTheme {
                Column(Modifier.verticalScroll(rememberScrollState())) {
                    LabelScanRequiredCard(result(), { selected = it.id }, { added = true }, {})
                }
            }
        }
        compose.onNodeWithText("Ingredients recognized").assertExists()
        compose.onNodeWithText("This product is incomplete").assertDoesNotExist()
        compose.onNodeWithText("Nutrition information is still needed").assertDoesNotExist()
        compose.onNodeWithText("Product name not confirmed").assertDoesNotExist()
        compose.onNodeWithContentDescription("Sugar. Open ingredient details").performScrollTo().performClick()
        assertEquals("sugar", selected)
        compose.onNodeWithText("Add another photo").performScrollTo().performClick()
        assertTrue(added)
        assertNull(result().healthScore)
    }

    @Test fun emptyLabelResultShowsRetryGuidanceInBulgarianWithoutInventingIngredients() {
        compose.setContent {
            CompositionLocalProvider(LocalAppLanguage provides AppLanguage.BULGARIAN) {
                NutriGuardScannerTheme { LabelScanRequiredCard(result(false), {}, {}, {}) }
            }
        }
        compose.onNodeWithText("Няма получени съставки от това сканиране").assertExists()
        compose.onNodeWithText("Сървърът не върна съставки и не посочи конкретна причина. Можеш да опиташ отново.").assertExists()
        compose.onNodeWithText("Допълни със снимка").assertExists()
        compose.onNodeWithText("This product is incomplete").assertDoesNotExist()
    }

    @Test fun barcodeMissRetainsBarcodeGuidance() {
        compose.setContent {
            NutriGuardScannerTheme { LabelScanRequiredCard(result(false, false), {}, {}, {}) }
        }
        compose.onNodeWithText("Label Scan Needed").assertExists()
        compose.onNodeWithText("This product is incomplete").assertExists()
    }

    @Test fun syntheticProductHeadingIsSuppressed() {
        assertEquals(UNCONFIRMED_PRODUCT_NAME, partialProductDisplayName(" Scanned Label Product "))
        assertEquals("Actual product", partialProductDisplayName("Actual product"))
    }

    @Test fun successfulResultHidesLegacyIdentityPlaceholders() {
        listOf("", "Scanned Product", "Scanned Label Product", "Analyzed Brand", "Analyzed Food")
            .forEach { assertNull(productIdentityText(it)) }
        assertEquals("", productIdentitySubtitle("Analyzed Brand", "Analyzed Food"))
        assertEquals("Real brand", productIdentitySubtitle("Real brand", "Analyzed Food"))
        assertEquals("Real brand • Biscuits", productIdentitySubtitle("Real brand", "Biscuits"))
    }
}
