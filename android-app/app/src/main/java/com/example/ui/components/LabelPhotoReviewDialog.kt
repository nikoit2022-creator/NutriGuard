package com.example.ui.components

import android.graphics.Bitmap
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage

@Composable
fun LabelPhotoReviewDialog(
    photos: List<Bitmap>,
    maxPhotos: Int,
    onCamera: () -> Unit,
    onGallery: () -> Unit,
    onRemove: (Int) -> Unit,
    onAnalyze: () -> Unit,
    onCancel: () -> Unit
) {
    val bulgarian = LocalAppLanguage.current == AppLanguage.BULGARIAN
    Dialog(onDismissRequest = onCancel, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(
            modifier = Modifier.fillMaxWidth(0.96f),
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    if (bulgarian) "Снимки на етикета" else "Label photos",
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    if (bulgarian) "${photos.size} от $maxPhotos снимки. Добавете още или започнете разчитането." else "${photos.size} of $maxPhotos photos. Add more or start analysis.",
                    style = MaterialTheme.typography.bodyMedium
                )
                Text(
                    if (bulgarian) "Използвайте снимки от един и същ продукт." else "Use photos of the same product.",
                    style = MaterialTheme.typography.bodySmall
                )
                LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    itemsIndexed(photos) { index, photo ->
                        Box {
                            Image(
                                bitmap = photo.asImageBitmap(),
                                contentDescription = if (bulgarian) "Снимка ${index + 1}" else "Photo ${index + 1}",
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.size(100.dp)
                            )
                            IconButton(
                                onClick = { onRemove(index) },
                                modifier = Modifier.align(Alignment.TopEnd).size(32.dp).background(Color.Black.copy(alpha = 0.65f), RoundedCornerShape(16.dp))
                            ) {
                                Icon(Icons.Default.Close, contentDescription = if (bulgarian) "Премахни" else "Remove", tint = Color.White)
                            }
                        }
                    }
                }
                if (photos.size < maxPhotos) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = onCamera, modifier = Modifier.weight(1f)) {
                            Text(if (bulgarian) "Камера" else "Camera")
                        }
                        OutlinedButton(onClick = onGallery, modifier = Modifier.weight(1f)) {
                            Text(if (bulgarian) "Галерия" else "Gallery")
                        }
                    }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = onCancel) {
                        Text(if (bulgarian) "Отказ" else "Cancel")
                    }
                    Button(onClick = onAnalyze) {
                        Text(if (bulgarian) "Разчети" else "Analyze")
                    }
                }
            }
        }
    }
}
