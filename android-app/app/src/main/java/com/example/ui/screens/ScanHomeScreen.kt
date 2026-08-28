package com.example.ui.screens

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.ImageDecoder
import android.net.Uri
import android.os.Build
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow
import com.example.ui.viewmodel.MainViewModel
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import java.io.File
import kotlin.math.ceil
import kotlin.math.max

@Composable
fun ScanHomeScreen(
    viewModel: MainViewModel,
    onNavigateToResult: () -> Unit,
    onNavigateToLibrary: () -> Unit
) {
    val context = LocalContext.current
    val recentScans by viewModel.scanHistory.collectAsState()
    val isDark = isSystemInDarkTheme()

    var isOcrMode by remember { mutableStateOf(false) }
    var showManualEntry by remember { mutableStateOf(false) }
    var barcodeInput by remember { mutableStateOf("") }
    var rawTextInput by remember { mutableStateOf("") }
    var pendingCameraUri by remember { mutableStateOf<Uri?>(null) }

    val barcodeScannerOptions = remember {
        GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(
                Barcode.FORMAT_EAN_13,
                Barcode.FORMAT_EAN_8,
                Barcode.FORMAT_UPC_A,
                Barcode.FORMAT_UPC_E
            )
            .enableAutoZoom()
            .build()
    }
    val barcodeScanner = remember(context, barcodeScannerOptions) {
        GmsBarcodeScanning.getClient(context, barcodeScannerOptions)
    }
    val startBarcodeScan: () -> Unit = {
        barcodeScanner.startScan()
            .addOnSuccessListener { barcode ->
                val value = normalizeScannedBarcode(barcode.rawValue)
                if (value != null) {
                    viewModel.scanBarcode(value)
                    onNavigateToResult()
                } else {
                    Toast.makeText(context, "No barcode value was detected.", Toast.LENGTH_SHORT).show()
                }
            }
            .addOnCanceledListener {
                // Stay on the scan screen when the user closes the scanner.
            }
            .addOnFailureListener {
                Toast.makeText(context, "Unable to start barcode scanner.", Toast.LENGTH_SHORT).show()
            }
    }

    val imagePickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri ->
        uri?.let {
            try {
                val inputStream = context.contentResolver.openInputStream(it)
                val bitmap = BitmapFactory.decodeStream(inputStream)
                if (bitmap != null) {
                    viewModel.analyzeLabelImage(bitmap)
                    onNavigateToResult()
                }
            } catch (e: Exception) {
                e.printStackTrace()
            }
        }
    }

    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicture()
    ) { captured ->
        val uri = pendingCameraUri
        if (captured && uri != null) {
            try {
                val bitmap = decodeCapturedBitmap(context, uri)
                if (bitmap != null) {
                    viewModel.analyzeLabelImage(bitmap)
                    onNavigateToResult()
                } else {
                    Toast.makeText(context, "Unable to read the captured image.", Toast.LENGTH_SHORT).show()
                }
            } catch (_: Exception) {
                Toast.makeText(context, "Unable to process the captured image.", Toast.LENGTH_SHORT).show()
            }
        }
        pendingCameraUri = null
    }

    val startIngredientPhotoCapture: () -> Unit = {
        try {
            val photoFile = File.createTempFile("ingredient_label_", ".jpg", context.cacheDir)
            val uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                photoFile
            )
            pendingCameraUri = uri
            cameraLauncher.launch(uri)
        } catch (_: Exception) {
            pendingCameraUri = null
            Toast.makeText(context, "Unable to open the camera.", Toast.LENGTH_SHORT).show()
        }
    }

    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.lg)
    ) {
        // App Header
        item {
            Spacer(modifier = Modifier.height(NutriGuardSpacing.md))
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Column {
                    Text(
                        text = "NutriGuard",
                        style = MaterialTheme.typography.headlineLarge,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Text(
                        text = "Know what's really in your food",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }

                IconButton(
                    onClick = onNavigateToLibrary,
                    modifier = Modifier
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.surfaceVariant)
                ) {
                    Icon(
                        imageVector = Icons.Default.Search,
                        contentDescription = "Search Ingredient Database",
                        tint = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        // Primary Scan Hero Card
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(NutriGuardRadius.large),
                colors = CardDefaults.cardColors(
                    containerColor = if (isDark) MaterialTheme.colorScheme.surface else Color(0xFFF0FDF4)
                ),
                border = androidx.compose.foundation.BorderStroke(
                    1.dp,
                    if (isDark) MaterialTheme.colorScheme.outline else EmeraldPrimary.copy(alpha = 0.2f)
                )
            ) {
                Column(
                    modifier = Modifier.padding(20.dp),
                    horizontalAlignment = Alignment.CenterHorizontally
                ) {
                    // Mode Selector Pill
                    Row(
                        modifier = Modifier
                            .clip(RoundedCornerShape(NutriGuardRadius.pill))
                            .background(if (isDark) MaterialTheme.colorScheme.surfaceVariant else Color(0xFFE2E8F0))
                            .padding(4.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(NutriGuardRadius.pill))
                                .background(if (!isOcrMode) EmeraldPrimary else Color.Transparent)
                                .clickable { isOcrMode = false }
                                .padding(horizontal = 16.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "Barcode",
                                fontSize = 13.sp,
                                fontWeight = if (!isOcrMode) FontWeight.SemiBold else FontWeight.Normal,
                                color = if (!isOcrMode) Color.White else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }

                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(NutriGuardRadius.pill))
                                .background(if (isOcrMode) EmeraldPrimary else Color.Transparent)
                                .clickable { isOcrMode = true }
                                .padding(horizontal = 16.dp, vertical = 6.dp)
                        ) {
                            Text(
                                text = "Ingredient Label",
                                fontSize = 13.sp,
                                fontWeight = if (isOcrMode) FontWeight.SemiBold else FontWeight.Normal,
                                color = if (isOcrMode) Color.White else MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(20.dp))

                    // Scanner Viewfinder Graphic
                    Box(
                        modifier = Modifier
                            .size(130.dp)
                            .clip(RoundedCornerShape(20.dp))
                            .background(if (isDark) Color(0xFF1E2D42) else EmeraldPrimary.copy(alpha = 0.08f))
                            .border(
                                2.dp,
                                EmeraldPrimary.copy(alpha = 0.6f),
                                RoundedCornerShape(20.dp)
                            )
                            .clickable {
                                if (isOcrMode) {
                                    startIngredientPhotoCapture()
                                } else {
                                    startBarcodeScan()
                                }
                            },
                        contentAlignment = Alignment.Center
                    ) {
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.Center
                        ) {
                            Icon(
                                imageVector = if (isOcrMode) Icons.Default.DocumentScanner else Icons.Default.QrCodeScanner,
                                contentDescription = "Scanner",
                                tint = EmeraldPrimary,
                                modifier = Modifier.size(48.dp)
                            )
                            Spacer(modifier = Modifier.height(6.dp))
                            Text(
                                text = "Tap to Scan",
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Medium,
                                color = EmeraldPrimary
                            )
                        }
                    }

                    Spacer(modifier = Modifier.height(18.dp))

                    Text(
                        text = if (isOcrMode) "Point camera at food ingredient list" else "Scan barcode on any food packaging",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )

                    Spacer(modifier = Modifier.height(18.dp))

                    // Primary Action Button
                    Button(
                        onClick = {
                            if (isOcrMode) {
                                startIngredientPhotoCapture()
                            } else {
                                startBarcodeScan()
                            }
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(50.dp),
                        shape = RoundedCornerShape(NutriGuardRadius.medium),
                        colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                    ) {
                        Icon(
                            imageVector = if (isOcrMode) Icons.Default.CameraAlt else Icons.Default.QrCodeScanner,
                            contentDescription = null,
                            modifier = Modifier.size(20.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = if (isOcrMode) "Scan Ingredient Label" else "Scan Barcode",
                            fontSize = 15.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }

                    Spacer(modifier = Modifier.height(10.dp))

                    // Secondary Action: Upload Photo
                    OutlinedButton(
                        onClick = { imagePickerLauncher.launch("image/*") },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(44.dp),
                        shape = RoundedCornerShape(NutriGuardRadius.medium),
                        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                    ) {
                        Icon(
                            imageVector = Icons.Default.PhotoLibrary,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = "Upload Photo from Gallery",
                            fontSize = 13.sp,
                            color = MaterialTheme.colorScheme.onSurface
                        )
                    }
                }
            }
        }

        // Recent Scans Section (if available)
        if (recentScans.isNotEmpty()) {
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Text(
                        text = "Recent Scans",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                }

                Spacer(modifier = Modifier.height(8.dp))

                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    recentScans.take(3).forEach { scan ->
                        Card(
                            modifier = Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(NutriGuardRadius.medium))
                                .clickable {
                                    val barcode = scan.barcode
                                    if (!barcode.isNullOrBlank()) {
                                        viewModel.scanBarcode(barcode)
                                        onNavigateToResult()
                                    }
                                },
                            shape = RoundedCornerShape(NutriGuardRadius.medium),
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                        ) {
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .padding(14.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = scan.productName,
                                        style = MaterialTheme.typography.titleSmall,
                                        fontWeight = FontWeight.SemiBold,
                                        color = MaterialTheme.colorScheme.onSurface
                                    )
                                    Text(
                                        text = "${scan.brand} • ${scan.scanType}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant
                                    )
                                }

                                val score = scan.healthScore
                                val scoreColor = when {
                                    score >= 80 -> RiskGreen
                                    score >= 60 -> RiskYellow
                                    score >= 40 -> RiskOrange
                                    else -> RiskRed
                                }

                                Box(
                                    modifier = Modifier
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(scoreColor.copy(alpha = 0.15f))
                                        .padding(horizontal = 10.dp, vertical = 4.dp)
                                ) {
                                    Text(
                                        text = "$score/100",
                                        fontSize = 12.sp,
                                        fontWeight = FontWeight.SemiBold,
                                        color = scoreColor
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        // Manual Input (Collapsible)
        item {
            Card(
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(NutriGuardRadius.large),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
            ) {
                Column(modifier = Modifier.padding(16.dp)) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clickable { showManualEntry = !showManualEntry },
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(
                                text = "Manual Barcode or Text Lookup",
                                style = MaterialTheme.typography.titleMedium,
                                fontWeight = FontWeight.SemiBold,
                                color = MaterialTheme.colorScheme.onSurface
                            )
                            Text(
                                text = "Type barcode number or paste ingredient list",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }

                        Icon(
                            imageVector = if (showManualEntry) Icons.Default.KeyboardArrowUp else Icons.Default.KeyboardArrowDown,
                            contentDescription = if (showManualEntry) "Collapse" else "Expand",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }

                    AnimatedVisibility(visible = showManualEntry) {
                        Column(modifier = Modifier.padding(top = 14.dp)) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                OutlinedTextField(
                                    value = barcodeInput,
                                    onValueChange = { barcodeInput = it },
                                    placeholder = { Text("e.g. 012345678905", fontSize = 13.sp) },
                                    modifier = Modifier.weight(1f),
                                    singleLine = true,
                                    shape = RoundedCornerShape(NutriGuardRadius.small)
                                )
                                Spacer(modifier = Modifier.width(8.dp))
                                Button(
                                    onClick = {
                                        if (barcodeInput.isNotBlank()) {
                                            viewModel.scanBarcode(barcodeInput)
                                            onNavigateToResult()
                                        }
                                    },
                                    shape = RoundedCornerShape(NutriGuardRadius.small),
                                    colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                                ) {
                                    Text("Lookup")
                                }
                            }

                            Spacer(modifier = Modifier.height(12.dp))

                            OutlinedTextField(
                                value = rawTextInput,
                                onValueChange = { rawTextInput = it },
                                placeholder = { Text("Paste ingredients list (e.g. Water, Sugar, E951, E102)...", fontSize = 13.sp) },
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .height(85.dp),
                                shape = RoundedCornerShape(NutriGuardRadius.small)
                            )

                            Spacer(modifier = Modifier.height(8.dp))

                            Button(
                                onClick = {
                                    if (rawTextInput.isNotBlank()) {
                                        viewModel.analyzeOcrText(rawTextInput)
                                        onNavigateToResult()
                                    }
                                },
                                modifier = Modifier.align(Alignment.End),
                                shape = RoundedCornerShape(NutriGuardRadius.small),
                                colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                            ) {
                                Text("Analyze Text")
                            }
                        }
                    }
                }
            }
        }

        // Sample Test Products
        item {
            Text(
                text = "Sample Test Products",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )

            Spacer(modifier = Modifier.height(8.dp))

            LazyRow(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                item {
                    SampleProductCard(
                        title = "FizzMax Zero",
                        subtitle = "Aspartame, Tartrazine",
                        score = 32,
                        onClick = {
                            viewModel.scanBarcode("012345678905")
                            onNavigateToResult()
                        }
                    )
                }
                item {
                    SampleProductCard(
                        title = "Smoked Bacon",
                        subtitle = "Sodium Nitrite, MSG",
                        score = 24,
                        onClick = {
                            viewModel.scanBarcode("098765432109")
                            onNavigateToResult()
                        }
                    )
                }
                item {
                    SampleProductCard(
                        title = "Pure Oat Bar",
                        subtitle = "Organic Rolled Oats",
                        score = 92,
                        onClick = {
                            viewModel.scanBarcode("055555555555")
                            onNavigateToResult()
                        }
                    )
                }
                item {
                    SampleProductCard(
                        title = "HyperDrive Energy",
                        subtitle = "HFCS, Titanium Dioxide",
                        score = 18,
                        onClick = {
                            viewModel.scanBarcode("077777777777")
                            onNavigateToResult()
                        }
                    )
                }
            }
        }

        item {
            Spacer(modifier = Modifier.height(20.dp))
        }
    }
}

@Composable
private fun SampleProductCard(
    title: String,
    subtitle: String,
    score: Int,
    onClick: () -> Unit
) {
    val scoreColor = when {
        score >= 80 -> RiskGreen
        score >= 60 -> RiskYellow
        score >= 40 -> RiskOrange
        else -> RiskRed
    }

    Card(
        modifier = Modifier
            .width(160.dp)
            .clip(RoundedCornerShape(NutriGuardRadius.medium))
            .clickable { onClick() },
        shape = RoundedCornerShape(NutriGuardRadius.medium),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(6.dp))
                        .background(scoreColor.copy(alpha = 0.15f))
                        .padding(horizontal = 7.dp, vertical = 2.dp)
                ) {
                    Text(
                        text = "$score",
                        fontSize = 11.sp,
                        fontWeight = FontWeight.Bold,
                        color = scoreColor
                    )
                }

                Icon(
                    imageVector = Icons.Default.ChevronRight,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
                    modifier = Modifier.size(16.dp)
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = title,
                style = MaterialTheme.typography.titleSmall,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurface
            )

            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1
            )
        }
    }
}


internal fun normalizeScannedBarcode(rawValue: String?): String? =
    rawValue?.trim()?.takeIf { it.isNotEmpty() }

internal fun decodeCapturedBitmap(
    context: Context,
    uri: Uri,
    maxDimension: Int = 2048
): Bitmap? {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
        val source = ImageDecoder.createSource(context.contentResolver, uri)
        return ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
            val largestDimension = max(info.size.width, info.size.height)
            val sampleSize = ceil(largestDimension.toDouble() / maxDimension)
                .toInt()
                .coerceAtLeast(1)
            decoder.setTargetSampleSize(sampleSize)
            decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
        }
    }

    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    context.contentResolver.openInputStream(uri)?.use {
        BitmapFactory.decodeStream(it, null, bounds)
    }
    val largestDimension = max(bounds.outWidth, bounds.outHeight)
    var sampleSize = 1
    while (largestDimension / sampleSize > maxDimension) {
        sampleSize *= 2
    }
    val options = BitmapFactory.Options().apply { inSampleSize = sampleSize }
    return context.contentResolver.openInputStream(uri)?.use {
        BitmapFactory.decodeStream(it, null, options)
    }
}
