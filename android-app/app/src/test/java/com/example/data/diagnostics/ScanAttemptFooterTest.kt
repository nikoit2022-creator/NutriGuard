package com.example.data.diagnostics

import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.performClick
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.ClipboardManager
import androidx.compose.ui.text.AnnotatedString
import com.example.ui.components.ScanAttemptFooter
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScanAttemptFooterTest {
    @Test fun differentServerIdIsVisibleAlongsideClientId() {
        compose.setContent { ScanAttemptFooter(ScanAttemptId("0000123456789012"), enabled = true,
            serverId = ScanAttemptId("1111222233334444")) }
        compose.onNodeWithText("Scan ID: 0000 1234 5678 9012").assertIsDisplayed()
        compose.onNodeWithText("Different server ID: 1111 2222 3333 4444").assertIsDisplayed()
    }
    @get:Rule val compose = createComposeRule()
    @Test fun bulgarianFooterCopiesFullIdIncludingZeros() {
        var copied: AnnotatedString? = null
        val clipboard = object : ClipboardManager {
            override fun getText() = copied
            override fun setText(annotatedString: AnnotatedString) { copied = annotatedString }
        }
        compose.setContent {
            CompositionLocalProvider(LocalAppLanguage provides AppLanguage.BULGARIAN, LocalClipboardManager provides clipboard) {
                ScanAttemptFooter(ScanAttemptId("0000123456789012"), enabled = true)
            }
        }
        compose.onNodeWithText("ID на сканирането: 0000 1234 5678 9012").assertIsDisplayed()
        compose.onNodeWithText("Копирай ID").performClick()
        assertEquals("0000123456789012", copied?.text)
    }

    @Test fun englishFooterHasReadableId() {
        compose.setContent { ScanAttemptFooter(ScanAttemptId("0000123456789012"), enabled = true) }
        compose.onNodeWithText("Scan ID: 0000 1234 5678 9012").assertIsDisplayed()
        compose.onNodeWithText("Copy ID").assertIsDisplayed()
    }

    @Test fun disabledFooterDoesNotRender() {
        compose.setContent { ScanAttemptFooter(ScanAttemptId("0000123456789012"), enabled = false) }
        compose.onNodeWithText("Copy ID").assertDoesNotExist()
    }
}
