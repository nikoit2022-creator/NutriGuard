package com.example.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.AnnotatedString
import com.example.BuildConfig
import com.example.data.diagnostics.ScanAttemptId
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage

/** Place inside the terminal result's bottom content, never overlay the result. */
@Composable
fun ScanAttemptFooter(id: ScanAttemptId, enabled: Boolean = BuildConfig.DEBUG, serverId: ScanAttemptId? = null) {
    if (!enabled) return
    val bg = LocalAppLanguage.current == AppLanguage.BULGARIAN
    val clipboard = LocalClipboardManager.current
    Column(Modifier.fillMaxWidth()) {
        Text(
            text = (if (bg) "ID на сканирането: " else "Scan ID: ") + id.display,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )
        TextButton(onClick = { clipboard.setText(AnnotatedString(id.value)) }) {
            Text(if (bg) "Копирай ID" else "Copy ID")
        }
        if (serverId != null && serverId != id) {
            Text((if (bg) "Различно ID от сървъра: " else "Different server ID: ") + serverId.display,
                style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = { clipboard.setText(AnnotatedString(serverId.value)) }) {
                Text(if (bg) "Копирай ID от сървъра" else "Copy server ID")
            }
        }
    }
}
