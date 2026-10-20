package com.example.ui.components

import android.graphics.Bitmap
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.example.ui.i18n.AppLanguage
import com.example.ui.i18n.LocalAppLanguage
import kotlin.math.roundToInt

private const val MIN_CROP = 0.08f

private data class CropArea(val left: Float, val top: Float, val right: Float, val bottom: Float)
private enum class DragPart { TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT, MOVE }

private fun imageBounds(width: Float, height: Float, bitmap: Bitmap): Rect {
    val scale = minOf(width / bitmap.width, height / bitmap.height)
    val imageWidth = bitmap.width * scale
    val imageHeight = bitmap.height * scale
    val left = (width - imageWidth) / 2f
    val top = (height - imageHeight) / 2f
    return Rect(left, top, left + imageWidth, top + imageHeight)
}

private fun cropBitmap(bitmap: Bitmap, area: CropArea): Bitmap {
    val left = (area.left * bitmap.width).roundToInt().coerceIn(0, bitmap.width - 1)
    val top = (area.top * bitmap.height).roundToInt().coerceIn(0, bitmap.height - 1)
    val right = (area.right * bitmap.width).roundToInt().coerceIn(left + 1, bitmap.width)
    val bottom = (area.bottom * bitmap.height).roundToInt().coerceIn(top + 1, bitmap.height)
    return Bitmap.createBitmap(bitmap, left, top, right - left, bottom - top)
}

@Composable
fun LabelImageCropDialog(bitmap: Bitmap, onConfirm: (Bitmap) -> Unit, onDismiss: () -> Unit) {
    val bulgarian = LocalAppLanguage.current == AppLanguage.BULGARIAN
    var area by remember(bitmap) { mutableStateOf(CropArea(0.03f, 0.03f, 0.97f, 0.97f)) }
    val image = remember(bitmap) { bitmap.asImageBitmap() }
    var dragPart by remember(bitmap) { mutableStateOf<DragPart?>(null) }

    Dialog(onDismissRequest = onDismiss, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(
            modifier = Modifier.fillMaxWidth(0.96f).fillMaxHeight(0.84f),
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    if (bulgarian) "Изрежете текста от етикета" else "Crop the label text",
                    style = MaterialTheme.typography.titleLarge
                )
                Text(
                    if (bulgarian) "Преместете ъглите, за да оставите само нужния текст." else "Move the corners to keep only the relevant text.",
                    style = MaterialTheme.typography.bodyMedium
                )
                Canvas(
                    Modifier.weight(1f).fillMaxWidth().pointerInput(bitmap) {
                        detectDragGestures(
                            onDragStart = { point ->
                                val bounds = imageBounds(size.width.toFloat(), size.height.toFloat(), bitmap)
                                val corners = listOf(
                                    DragPart.TOP_LEFT to Offset(bounds.left + area.left * bounds.width, bounds.top + area.top * bounds.height),
                                    DragPart.TOP_RIGHT to Offset(bounds.left + area.right * bounds.width, bounds.top + area.top * bounds.height),
                                    DragPart.BOTTOM_LEFT to Offset(bounds.left + area.left * bounds.width, bounds.top + area.bottom * bounds.height),
                                    DragPart.BOTTOM_RIGHT to Offset(bounds.left + area.right * bounds.width, bounds.top + area.bottom * bounds.height)
                                )
                                val nearest = corners.minByOrNull { (_, corner) -> (corner - point).getDistance() }
                                val hitRadius = 40.dp.toPx()
                                dragPart = if (nearest != null && (nearest.second - point).getDistance() <= hitRadius) {
                                    nearest.first
                                } else {
                                    val left = bounds.left + area.left * bounds.width
                                    val right = bounds.left + area.right * bounds.width
                                    val top = bounds.top + area.top * bounds.height
                                    val bottom = bounds.top + area.bottom * bounds.height
                                    if (point.x in left..right && point.y in top..bottom) DragPart.MOVE else null
                                }
                            },
                            onDragEnd = { dragPart = null },
                            onDragCancel = { dragPart = null },
                            onDrag = { change, delta ->
                                val part = dragPart ?: return@detectDragGestures
                                change.consume()
                                val bounds = imageBounds(size.width.toFloat(), size.height.toFloat(), bitmap)
                                val dx = delta.x / bounds.width
                                val dy = delta.y / bounds.height
                                area = when (part) {
                                    DragPart.TOP_LEFT -> area.copy(
                                        left = (area.left + dx).coerceIn(0f, area.right - MIN_CROP),
                                        top = (area.top + dy).coerceIn(0f, area.bottom - MIN_CROP)
                                    )
                                    DragPart.TOP_RIGHT -> area.copy(
                                        right = (area.right + dx).coerceIn(area.left + MIN_CROP, 1f),
                                        top = (area.top + dy).coerceIn(0f, area.bottom - MIN_CROP)
                                    )
                                    DragPart.BOTTOM_LEFT -> area.copy(
                                        left = (area.left + dx).coerceIn(0f, area.right - MIN_CROP),
                                        bottom = (area.bottom + dy).coerceIn(area.top + MIN_CROP, 1f)
                                    )
                                    DragPart.BOTTOM_RIGHT -> area.copy(
                                        right = (area.right + dx).coerceIn(area.left + MIN_CROP, 1f),
                                        bottom = (area.bottom + dy).coerceIn(area.top + MIN_CROP, 1f)
                                    )
                                    DragPart.MOVE -> {
                                        val width = area.right - area.left
                                        val height = area.bottom - area.top
                                        val left = (area.left + dx).coerceIn(0f, 1f - width)
                                        val top = (area.top + dy).coerceIn(0f, 1f - height)
                                        CropArea(left, top, left + width, top + height)
                                    }
                                }
                            }
                        )
                    }
                ) {
                    val bounds = imageBounds(size.width, size.height, bitmap)
                    drawImage(
                        image = image,
                        dstOffset = IntOffset(bounds.left.roundToInt(), bounds.top.roundToInt()),
                        dstSize = IntSize(bounds.width.roundToInt(), bounds.height.roundToInt())
                    )
                    val left = bounds.left + area.left * bounds.width
                    val top = bounds.top + area.top * bounds.height
                    val right = bounds.left + area.right * bounds.width
                    val bottom = bounds.top + area.bottom * bounds.height
                    val shade = Color.Black.copy(alpha = 0.6f)
                    drawRect(shade, Offset(bounds.left, bounds.top), Size(bounds.width, top - bounds.top))
                    drawRect(shade, Offset(bounds.left, bottom), Size(bounds.width, bounds.bottom - bottom))
                    drawRect(shade, Offset(bounds.left, top), Size(left - bounds.left, bottom - top))
                    drawRect(shade, Offset(right, top), Size(bounds.right - right, bottom - top))
                    drawRect(Color.White, Offset(left, top), Size(right - left, bottom - top), style = Stroke(2.dp.toPx()))
                    listOf(Offset(left, top), Offset(right, top), Offset(left, bottom), Offset(right, bottom)).forEach {
                        drawCircle(Color.White, radius = 7.dp.toPx(), center = it)
                    }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    TextButton(onClick = onDismiss) {
                        Text(if (bulgarian) "Пропусни" else "Skip photo")
                    }
                    Button(onClick = { onConfirm(cropBitmap(bitmap, area)) }) {
                        Text(if (bulgarian) "Продължи" else "Continue")
                    }
                }
            }
        }
    }
}
