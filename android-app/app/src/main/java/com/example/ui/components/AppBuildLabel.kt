package com.example.ui.components

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import com.example.BuildConfig
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage

/** Build identity is captured at compilation, not read from a server or device clock. */
@Composable
fun AppBuildLabel() {
    val label = if (LocalAppLanguage.current == AppLanguage.BULGARIAN) "Версия" else "Version"
    Text(
        text = "$label ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE}) • ${BuildConfig.SOURCE_REVISION}",
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant
    )
}
