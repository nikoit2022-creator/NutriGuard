package com.example.ui.components

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import kotlin.math.roundToInt

/** Places cropped label regions on one white page for the single-image scan API. */
fun combineLabelPhotos(photos: List<Bitmap>): Bitmap {
    require(photos.isNotEmpty())
    if (photos.size == 1) return photos.single()

    val padding = 20
    val tiles = photos.map { photo ->
        val scale = minOf(1f, 1700f / photo.width, 1700f / photo.height)
        val width = (photo.width * scale).roundToInt().coerceAtLeast(1)
        val height = (photo.height * scale).roundToInt().coerceAtLeast(1)
        width to height
    }
    val pageWidth = tiles.maxOf { it.first } + padding * 2
    val pageHeight = tiles.sumOf { it.second } + padding * (photos.size + 1)
    val page = Bitmap.createBitmap(pageWidth, pageHeight, Bitmap.Config.RGB_565)
    val canvas = Canvas(page)
    canvas.drawColor(Color.WHITE)
    val paint = Paint(Paint.FILTER_BITMAP_FLAG)
    var top = padding.toFloat()
    photos.zip(tiles).forEach { (photo, dimensions) ->
        val (width, height) = dimensions
        val left = (pageWidth - width) / 2f
        canvas.drawBitmap(photo, null, RectF(left, top, left + width, top + height), paint)
        top += height + padding
    }
    return page
}
