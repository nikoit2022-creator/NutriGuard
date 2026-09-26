package com.example.ui.i18n

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.WindowInsetsSides
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.only
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.ui.theme.ScannerPageBackground

/** Custom Scaffold bars must consume their own status-bar/display-cutout insets. */
@Composable
internal fun LanguageTopBar(
    language: AppLanguage,
    onLanguageChange: (AppLanguage) -> Unit,
    insets: WindowInsets = WindowInsets.safeDrawing.only(WindowInsetsSides.Top + WindowInsetsSides.Horizontal)
) {
    Box(
        modifier = Modifier.fillMaxWidth()
            .background(ScannerPageBackground)
            .windowInsetsPadding(insets)
            .padding(horizontal = 16.dp, vertical = 6.dp),
        contentAlignment = Alignment.CenterEnd
    ) {
        LanguageSwitcher(language, onLanguageChange)
    }
}
