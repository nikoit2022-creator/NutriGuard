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
import androidx.compose.material.icons.filled.ChevronRight
import androidx.compose.material.icons.filled.DocumentScanner
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SearchOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
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
import com.example.data.remote.dto.cleanOrNull
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow
import com.example.ui.viewmodel.BarcodeLookupUiState
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
    val barcodeLookupState by viewModel.barcodeLookupState.collectAsState()
    val pendingBarcode by viewModel.pendingBarcode.collectAsState()
    val isDark = isSystemInDarkTheme()

    var isOcrMode by remember { mutableStateOf(false) }
    var showManualEntry by remember { mutableStateOf(false) }
    var barcodeInput by remember { mutableStateOf("") }
    var rawTextInput by remember { mutableStateOf("") }
    var pendingCameraUri by remember { mutableStateOf<Uri?>(null) }
    // The app-owned File backing pendingCameraUri, tracked separately so
    // it can be deleted -- a gallery-picked content:// Uri (imagePickerLauncher,
    // below) never has one of these and must never be deleted by this screen.
    var pendingCameraFile by remember { mutableStateOf<File?>(null) }
    var lastSubmittedBarcode by remember { mutableStateOf<String?>(null) }

    // If this screen is disposed (e.g. the user navigates away) while an
    // ingredient-label capture was launched but its result was never
    // delivered, the temp file would otherwise leak forever. Safe to run
    // unconditionally: cameraLauncher's own callback clears
    // pendingCameraFile to null as the very first thing it does, before
    // it reads the file, so if this still sees a non-null file, nothing
    // is actively reading it.
    DisposableEffect(Unit) {
        onDispose {
            pendingCameraFile?.delete()
            pendingCameraFile = null
            pendingCameraUri = null
        }
    }

    val isSearchingBarcode = barcodeLookupState is BarcodeLookupUiState.Searching

    // Navigate to Product Details exactly once per successful lookup --
    // never eagerly, and never for a LabelScanRequired/Failed outcome
    // (see MainViewModel.scanBarcode's docs).
    LaunchedEffect(viewModel) {
        viewModel.navigateToResultEvent.collect {
            onNavigateToResult()
        }
    }

    // Single entry point for every barcode-triggered lookup (scanner,
    // manual entry, recent scans, sample cards): stays on this screen
    // and shows a "Searching product..." state instead of navigating
    // immediately, and ignores a resubmission while one is in flight.
    val submitBarcode: (String) -> Unit = { value ->
        if (!isSearchingBarcode) {
            lastSubmittedBarcode = value
            viewModel.scanBarcode(value)
        }
    }

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
                    submitBarcode(value)
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

    // Both the gallery pick and the camera capture below call the SAME
    // viewModel.analyzeLabelImage(bitmap) -- it internally attaches
    // `pendingBarcode` (if any) via the backend's optional multipart
    // `barcode` field, and drives the SAME Searching/LabelScanRequired/
    // Failed card this screen already renders for a barcode lookup (see
    // MainViewModel.BarcodeLookupUiState's docs). Neither launcher
    // navigates directly anymore -- navigation only happens via
    // navigateToResultEvent, on a genuine complete success, exactly
    // like the barcode flow (requirement: gallery-picked photos must
    // combine with a pending barcode exactly like camera photos do).
    val imagePickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent()
    ) { uri ->
        uri?.let {
            try {
                val inputStream = context.contentResolver.openInputStream(it)
                val bitmap = BitmapFactory.decodeStream(inputStream)
                if (bitmap != null) {
                    viewModel.analyzeLabelImage(bitmap)
                } else {
                    Toast.makeText(context, "Unable to read the selected image.", Toast.LENGTH_SHORT).show()
                }
            } catch (e: Exception) {
                Toast.makeText(context, "Unable to process the selected image.", Toast.LENGTH_SHORT).show()
            }
        }
    }

    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicture()
    ) { captured ->
        val uri = pendingCameraUri
        val file = pendingCameraFile
        pendingCameraUri = null
        pendingCameraFile = null

        if (captured && uri != null) {
            try {
                val bitmap = decodeCapturedBitmap(context, uri)
                if (bitmap != null) {
                    viewModel.analyzeLabelImage(bitmap)
                } else {
                    Toast.makeText(context, "Unable to read the captured image.", Toast.LENGTH_SHORT).show()
                }
            } catch (_: Exception) {
                Toast.makeText(context, "Unable to process the captured image.", Toast.LENGTH_SHORT).show()
            } finally {
                // decodeCapturedBitmap has fully read the file into `bitmap`
                // by this point (or the read/decode itself is what failed) --
                // either way the on-disk copy is no longer needed.
                file?.delete()
            }
        } else {
            // Cancelled (or captured with no URI, which shouldn't happen in
            // practice): nothing was ever read from the file.
            file?.delete()
        }
    }

    val startIngredientPhotoCapture: () -> Unit = {
        var createdFile: File? = null
        try {
            val photoFile = File.createTempFile("ingredient_label_", ".jpg", context.cacheDir)
            createdFile = photoFile
            val uri = FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                photoFile
            )
            pendingCameraFile = photoFile
            pendingCameraUri = uri
            cameraLauncher.launch(uri)
        } catch (_: Exception) {
            createdFile?.delete()
            pendingCameraFile = null
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
                            .clickable(enabled = !isSearchingBarcode) {
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

        // Barcode/label-image lookup state: stays on this screen the
        // whole time -- see MainViewModel.BarcodeLookupUiState.
        item {
            when (val lookupState = barcodeLookupState) {
                is BarcodeLookupUiState.Searching -> {
                    BarcodeSearchingCard(isAnalyzingPhoto = pendingBarcode != null || isOcrMode)
                }

                is BarcodeLookupUiState.LabelScanRequired -> {
                    LabelScanRequiredCard(
                        state = lookupState,
                        onScanLabel = {
                            viewModel.dismissBarcodeLookupState()
                            isOcrMode = true
                            startIngredientPhotoCapture()
                        },
                        onDismiss = { viewModel.cancelPendingScanFlow() }
                    )
                }

                is BarcodeLookupUiState.Failed -> {
                    BarcodeLookupFailedCard(
                        message = lookupState.message,
                        onRetry = {
                            viewModel.dismissBarcodeLookupState()
                            if (pendingBarcode != null) {
                                // The failure happened uploading a label
                                // photo for a still-pending barcode -- retry
                                // means "take that photo again", not
                                // "resubmit a barcode string" (there's no
                                // bitmap left to resend).
                                isOcrMode = true
                                startIngredientPhotoCapture()
                            } else {
                                (lastSubmittedBarcode ?: lookupState.barcode).let(submitBarcode)
                            }
                        },
                        onDismiss = { viewModel.dismissBarcodeLookupState() }
                    )
                }

                is BarcodeLookupUiState.Idle -> Unit
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
                                .clickable(enabled = !isSearchingBarcode) {
                                    val barcode = scan.barcode
                                    if (!barcode.isNullOrBlank()) {
                                        submitBarcode(barcode)
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
                                            submitBarcode(barcodeInput)
                                        }
                                    },
                                    enabled = !isSearchingBarcode,
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
                        onClick = { submitBarcode("012345678905") }
                    )
                }
                item {
                    SampleProductCard(
                        title = "Smoked Bacon",
                        subtitle = "Sodium Nitrite, MSG",
                        score = 24,
                        onClick = { submitBarcode("098765432109") }
                    )
                }
                item {
                    SampleProductCard(
                        title = "Pure Oat Bar",
                        subtitle = "Organic Rolled Oats",
                        score = 92,
                        onClick = { submitBarcode("055555555555") }
                    )
                }
                item {
                    SampleProductCard(
                        title = "HyperDrive Energy",
                        subtitle = "HFCS, Titanium Dioxide",
                        score = 18,
                        onClick = { submitBarcode("077777777777") }
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


/**
 * Shown on the Scan screen (never a navigation) while either
 * `POST /api/v1/scan/barcode` or `POST /api/v1/scan/label-image` is in
 * flight -- see `MainViewModel.BarcodeLookupUiState.Searching`, which
 * both share.
 */
@Composable
private fun BarcodeSearchingCard(isAnalyzingPhoto: Boolean) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(NutriGuardRadius.large),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            CircularProgressIndicator(
                color = EmeraldPrimary,
                strokeWidth = 2.5.dp,
                modifier = Modifier.size(22.dp)
            )
            Spacer(modifier = Modifier.width(14.dp))
            Column {
                Text(
                    text = if (isAnalyzingPhoto) "Analyzing label…" else "Searching product…",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface
                )
                Text(
                    text = if (isAnalyzingPhoto) {
                        "Reading nutrition and ingredient information from the photo"
                    } else {
                        "Checking our database and trusted product sources"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}

/**
 * The scan/rescan call-to-action label is ALWAYS this exact text --
 * never the backend's raw `suggestedAction` (a technical/generic hint,
 * not user-facing copy) and never varies by which evidence group is
 * still missing (see [missingEvidenceMessages] for that instead).
 */
internal const val SCAN_LABEL_ACTION_TEXT = "Scan label for more information"

/** Fixed copy for each still-missing evidence group -- shown ABOVE the (always-identical) action button so the user knows what the next photo needs to capture. Both can apply at once (requirement: sequential photos for each missing group, combined by the backend into the same product). */
internal fun missingEvidenceMessages(nutritionScanRequired: Boolean?, ingredientsScanRequired: Boolean?): List<String> =
    buildList {
        if (nutritionScanRequired == true) add("Nutrition information is still needed")
        if (ingredientsScanRequired == true) add("Ingredient information is still needed")
    }

/**
 * Shown when the backend answers `PRODUCT_NOT_FOUND` with
 * `details.labelScanRequired = true` -- the barcode/photo is unknown,
 * or was found but its data is too incomplete for a confident
 * analysis. Never navigates anywhere on its own; the user stays on the
 * Scan workflow and either scans the label (attaching `pendingBarcode`
 * automatically, see `MainViewModel.analyzeLabelImage`) or dismisses
 * the whole flow.
 *
 * Never renders a Health Score here -- [state.healthScore] is only
 * ever consulted for [state.healthScoreAvailable] purposes and is
 * otherwise ignored; a `null`/unavailable score must never be shown as
 * `0`, and this card never shows a numeric score in the first place.
 */
@Composable
private fun LabelScanRequiredCard(
    state: BarcodeLookupUiState.LabelScanRequired,
    onScanLabel: () -> Unit,
    onDismiss: () -> Unit
) {
    val identity = state.discoveredIdentity
    val identityName = identity?.productName?.cleanOrNull()
    val identityBrand = identity?.brand?.cleanOrNull()
    val missingMessages = missingEvidenceMessages(state.nutritionScanRequired, state.ingredientsScanRequired)
    val verifiedIngredientNames = state.ingredients.mapNotNull { it.commonName.cleanOrNull() }

    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(NutriGuardRadius.large),
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.4f)
        ),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = Icons.Default.SearchOff,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant
                )
                Spacer(modifier = Modifier.width(10.dp))
                Text(
                    text = "Label Scan Needed",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            // Only ever a safe, non-placeholder identity -- see
            // DiscoveredIdentity/BackendErrorDetailsDto parsing, which
            // already filters "null"/"None"/blank values; cleanOrNull()
            // here is a deliberate second, UI-side layer of the same
            // defense (never trust a single filtering pass for text
            // shown directly to the user).
            if (identityName != null) {
                Text(
                    text = buildString {
                        append(identityName)
                        if (identityBrand != null) append(" • $identityBrand")
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    color = MaterialTheme.colorScheme.onSurface
                )
                Spacer(modifier = Modifier.height(4.dp))
            }

            Text(
                text = state.reason,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            // Already-verified evidence -- never discarded just because
            // the OTHER evidence group is still missing.
            if (verifiedIngredientNames.isNotEmpty()) {
                Spacer(modifier = Modifier.height(10.dp))
                Text(
                    text = "Verified ingredients so far",
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.primary
                )
                Spacer(modifier = Modifier.height(2.dp))
                Text(
                    text = verifiedIngredientNames.joinToString(", "),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }

            // What's still needed -- one line per missing evidence group,
            // so two sequential photos (nutrition panel, then ingredient
            // list, or vice versa) each know what to capture next.
            if (missingMessages.isNotEmpty()) {
                Spacer(modifier = Modifier.height(10.dp))
                Column {
                    missingMessages.forEach { message ->
                        Text(
                            text = message,
                            style = MaterialTheme.typography.bodySmall,
                            fontWeight = FontWeight.Medium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                }
            }

            Spacer(modifier = Modifier.height(14.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = onScanLabel,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(NutriGuardRadius.small),
                    colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                ) {
                    Icon(
                        imageVector = Icons.Default.DocumentScanner,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(SCAN_LABEL_ACTION_TEXT, fontSize = 13.sp)
                }
                TextButton(onClick = onDismiss) {
                    Text("Dismiss")
                }
            }
        }
    }
}

/**
 * Shown for a network failure, timeout, or unexpected server/parse
 * error from the barcode lookup -- distinct from
 * [BarcodeLookupUiState.LabelScanRequired] (see `MainViewModel`'s
 * catch blocks): this always offers Retry, never "Scan Ingredient
 * Label", since the barcode itself was never actually resolved either
 * way.
 */
@Composable
private fun BarcodeLookupFailedCard(
    message: String,
    onRetry: () -> Unit,
    onDismiss: () -> Unit
) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(NutriGuardRadius.large),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.error.copy(alpha = 0.4f))
    ) {
        Column(modifier = Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = Icons.Default.ErrorOutline,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.error
                )
                Spacer(modifier = Modifier.width(10.dp))
                Text(
                    text = "Couldn't Complete Lookup",
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface
                )
            }

            Spacer(modifier = Modifier.height(8.dp))

            Text(
                text = message.cleanOrNull() ?: "Something went wrong. Please try again.",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )

            Spacer(modifier = Modifier.height(14.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = onRetry,
                    modifier = Modifier.weight(1f),
                    shape = RoundedCornerShape(NutriGuardRadius.small),
                    colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                ) {
                    Text("Retry", fontSize = 13.sp)
                }
                TextButton(onClick = onDismiss) {
                    Text("Dismiss")
                }
            }
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
