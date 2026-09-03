package com.example.ui.screens

import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import com.example.data.remote.dto.cleanOrNull
import com.example.ui.components.DietaryBadgesRow
import com.example.ui.components.HealthScoreGauge
import com.example.ui.components.IngredientDetailBottomSheet
import com.example.ui.components.PersonalizedWarningCard
import com.example.ui.theme.EmeraldPrimary
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.NutriGuardScannerTheme
import com.example.ui.theme.NutriGuardSpacing
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow
import com.example.ui.theme.ScannerHeroMiddle
import com.example.ui.theme.ScannerHeroStart
import com.example.ui.theme.ScannerPageBackground
import com.example.ui.theme.ScannerSlateMuted
import com.example.ui.theme.ScannerSlatePrimary
import com.example.ui.theme.ScannerSlateSecondary
import com.example.ui.theme.ScannerSoftBorder
import com.example.ui.theme.ScannerViolet
import com.example.ui.viewmodel.AnalysisUiState
import com.example.ui.viewmodel.BarcodeLookupUiState
import com.example.ui.viewmodel.MainViewModel
import java.io.File

@Composable
fun ProductDetailScreen(
    viewModel: MainViewModel,
    onBack: () -> Unit
) {
    val context = LocalContext.current
    val uiState by viewModel.analysisState.collectAsState()
    val barcodeLookupState by viewModel.barcodeLookupState.collectAsState()
    val pendingBarcode by viewModel.pendingBarcode.collectAsState()
    var selectedIngredientForDetail by remember { mutableStateOf<IngredientEntity?>(null) }
    var pendingCameraUri by remember { mutableStateOf<Uri?>(null) }
    var pendingCameraFile by remember { mutableStateOf<File?>(null) }
    var pendingEnrichmentBarcode by remember { mutableStateOf<String?>(null) }

    DisposableEffect(Unit) {
        onDispose {
            pendingCameraFile?.delete()
            pendingCameraFile = null
            pendingCameraUri = null
            pendingEnrichmentBarcode = null
        }
    }

    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicture()
    ) { captured ->
        val uri = pendingCameraUri
        val file = pendingCameraFile
        val barcode = pendingEnrichmentBarcode
        pendingCameraUri = null
        pendingCameraFile = null
        pendingEnrichmentBarcode = null

        if (captured && uri != null && barcode != null) {
            try {
                val bitmap = decodeCapturedBitmap(context, uri)
                if (bitmap != null) {
                    viewModel.analyzeLabelImageForBarcode(bitmap, barcode)
                } else {
                    Toast.makeText(context, "Unable to read the captured image.", Toast.LENGTH_SHORT).show()
                }
            } catch (_: Exception) {
                Toast.makeText(context, "Unable to process the captured image.", Toast.LENGTH_SHORT).show()
            } finally {
                file?.delete()
            }
        } else {
            file?.delete()
        }
    }

    val startProductEnrichmentCapture: (String) -> Unit = { barcode ->
        var createdFile: File? = null
        try {
            val photoFile = File.createTempFile("product_enrichment_", ".jpg", context.cacheDir)
            createdFile = photoFile
            val uri = androidx.core.content.FileProvider.getUriForFile(
                context,
                "${context.packageName}.fileprovider",
                photoFile
            )
            pendingCameraFile = photoFile
            pendingCameraUri = uri
            pendingEnrichmentBarcode = barcode
            cameraLauncher.launch(uri)
        } catch (_: Exception) {
            createdFile?.delete()
            pendingCameraFile = null
            pendingCameraUri = null
            pendingEnrichmentBarcode = null
            Toast.makeText(context, "Unable to open the camera.", Toast.LENGTH_SHORT).show()
        }
    }

    NutriGuardScannerTheme {
    when (val state = uiState) {
        is AnalysisUiState.Loading -> {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    modifier = Modifier.padding(24.dp)
                ) {
                    CircularProgressIndicator(
                        color = EmeraldPrimary,
                        modifier = Modifier.size(44.dp)
                    )
                    Spacer(modifier = Modifier.height(20.dp))
                    Text(
                        text = "Analyzing Product Ingredients...",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        color = MaterialTheme.colorScheme.onSurface
                    )
                    Spacer(modifier = Modifier.height(4.dp))
                    Text(
                        text = "Evaluating additives against EFSA, FDA & WHO safety guidelines",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                }
            }
        }

        is AnalysisUiState.Error -> {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(24.dp),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = "Analysis Unavailable",
                        style = MaterialTheme.typography.headlineMedium,
                        color = MaterialTheme.colorScheme.error,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Text(
                        text = state.message,
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                    )
                    Spacer(modifier = Modifier.height(20.dp))
                    Button(
                        onClick = onBack,
                        shape = RoundedCornerShape(NutriGuardRadius.medium),
                        colors = ButtonDefaults.buttonColors(containerColor = EmeraldPrimary)
                    ) {
                        Text("Back to Scanner")
                    }
                }
            }
        }

        is AnalysisUiState.Success -> {
            val analysis = state.analysis
            val product = analysis.product
            val highConcernCount = analysis.ingredients.count { it.riskLevel == RiskLevel.HIGH_CONCERN }
            val potentialConcernCount = analysis.ingredients.count { it.riskLevel == RiskLevel.POTENTIAL_CONCERN }
            val moderateCount = analysis.ingredients.count { it.riskLevel == RiskLevel.MODERATE }
            val safeCount = analysis.ingredients.count { it.riskLevel == RiskLevel.SAFE }

            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .background(ScannerPageBackground)
                    .padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(NutriGuardSpacing.lg)
            ) {
                // Product header adapted from the approved scanned-product submenu.
                item {
                    Spacer(modifier = Modifier.height(NutriGuardSpacing.sm))
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(NutriGuardRadius.hero))
                            .background(
                                Brush.linearGradient(
                                    colors = listOf(ScannerHeroStart, ScannerHeroMiddle)
                                )
                            )
                            .border(
                                1.dp,
                                Color.White.copy(alpha = 0.78f),
                                RoundedCornerShape(NutriGuardRadius.hero)
                            )
                    ) {
                        Column(modifier = Modifier.padding(18.dp)) {
                            Row(
                                modifier = Modifier.clickable(onClick = onBack),
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Icon(
                                    imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                                    contentDescription = "Back",
                                    tint = ScannerViolet,
                                    modifier = Modifier.size(18.dp)
                                )
                                Spacer(modifier = Modifier.width(4.dp))
                                Text(
                                    text = "Back",
                                    style = MaterialTheme.typography.bodySmall,
                                    color = ScannerViolet
                                )
                            }

                            Spacer(modifier = Modifier.height(18.dp))
                            Text(
                                text = product.brand.uppercase(),
                                style = MaterialTheme.typography.labelSmall,
                                fontWeight = FontWeight.Medium,
                                color = ScannerViolet,
                                letterSpacing = 1.2.sp
                            )
                            Text(
                                text = product.productName,
                                style = MaterialTheme.typography.headlineSmall,
                                fontWeight = FontWeight.Bold,
                                color = ScannerSlatePrimary
                            )
                            Text(
                                text = product.barcode,
                                style = MaterialTheme.typography.labelSmall,
                                color = ScannerSlateMuted
                            )

                            Spacer(modifier = Modifier.height(14.dp))
                            LazyRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                if (highConcernCount > 0) {
                                    item { RiskSummaryPill("$highConcernCount high concern", RiskRed) }
                                }
                                if (potentialConcernCount > 0) {
                                    item { RiskSummaryPill("$potentialConcernCount potential", RiskOrange) }
                                }
                                if (moderateCount > 0) {
                                    item { RiskSummaryPill("$moderateCount moderate", RiskYellow) }
                                }
                                if (safeCount > 0) {
                                    item { RiskSummaryPill("$safeCount low concern", RiskGreen) }
                                }
                            }
                        }
                    }
                }

                if (isEnrichableProductBarcode(product.barcode)) {
                    item {
                        val isUploading = barcodeLookupState is BarcodeLookupUiState.Searching
                        Column {
                            Button(
                                onClick = { startProductEnrichmentCapture(product.barcode) },
                                modifier = Modifier.fillMaxWidth(),
                                enabled = !isUploading,
                                shape = RoundedCornerShape(NutriGuardRadius.medium),
                                colors = ButtonDefaults.buttonColors(
                                    containerColor = ScannerViolet,
                                    contentColor = Color.White,
                                    disabledContainerColor = ScannerViolet.copy(alpha = 0.45f),
                                    disabledContentColor = Color.White.copy(alpha = 0.85f)
                                )
                            ) {
                                if (isUploading) {
                                    CircularProgressIndicator(
                                        modifier = Modifier.size(18.dp),
                                        color = Color.White,
                                        strokeWidth = 2.dp
                                    )
                                } else {
                                    Icon(
                                        imageVector = Icons.Default.CameraAlt,
                                        contentDescription = null,
                                        modifier = Modifier.size(18.dp)
                                    )
                                }
                                Spacer(modifier = Modifier.width(8.dp))
                                Text(
                                    text = if (isUploading) "Adding label information..." else "Scan ingredients and table",
                                    fontWeight = FontWeight.SemiBold
                                )
                            }

                            Text(
                                text = productEnrichmentStatusText(
                                    state = barcodeLookupState,
                                    isForThisProduct = pendingBarcode == product.barcode
                                ),
                                modifier = Modifier.padding(top = 6.dp, start = 4.dp, end = 4.dp),
                                style = MaterialTheme.typography.bodySmall,
                                color = if (barcodeLookupState is BarcodeLookupUiState.Failed && pendingBarcode == product.barcode) {
                                    MaterialTheme.colorScheme.error
                                } else {
                                    ScannerSlateSecondary
                                }
                            )
                        }
                    }
                }

                // Ingredient list is intentionally first, matching the approved submenu.
                item {
                    Text(
                        text = "INGREDIENTS • ${analysis.ingredients.size}",
                        style = MaterialTheme.typography.labelSmall,
                        fontWeight = FontWeight.Medium,
                        color = ScannerSlateMuted,
                        letterSpacing = 1.2.sp
                    )
                    Spacer(modifier = Modifier.height(8.dp))
                    Card(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(18.dp),
                        colors = CardDefaults.cardColors(containerColor = Color.White),
                        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
                        border = androidx.compose.foundation.BorderStroke(1.dp, ScannerSoftBorder)
                    ) {
                        Column {
                            analysis.ingredients.forEachIndexed { index, ingredient ->
                                ProductIngredientRow(
                                    ingredient = ingredient,
                                    onClick = { selectedIngredientForDetail = ingredient }
                                )
                                if (index < analysis.ingredients.lastIndex) {
                                    HorizontalDivider(
                                        color = ScannerSoftBorder,
                                        modifier = Modifier.padding(start = 42.dp)
                                    )
                                }
                            }
                        }
                    }
                }

                // Health Score Gauge
                item {
                    HealthScoreGauge(
                        score = analysis.healthScore,
                        novaGroup = product.novaGroup,
                        sugarGrams = product.sugarGrams,
                        sodiumMg = product.sodiumMg,
                        saturatedFatGrams = product.saturatedFatGrams
                    )
                }

                // Dietary Suitability Badges
                item {
                    Column {
                        Text(
                            text = "Dietary Suitability",
                            style = MaterialTheme.typography.titleMedium,
                            fontWeight = FontWeight.SemiBold,
                            color = ScannerSlatePrimary
                        )
                        Spacer(modifier = Modifier.height(8.dp))
                        DietaryBadgesRow(product = product)
                    }
                }

                // Personalized Profile Warning Banners (if any)
                if (analysis.warnings.isNotEmpty()) {
                    item {
                        PersonalizedWarningCard(warnings = analysis.warnings)
                    }
                }

                // Raw OCR Text Inspection (Collapsible / Subtle Card)
                if (product.rawIngredientText.isNotBlank()) {
                    item {
                        Card(
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(NutriGuardRadius.medium),
                            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
                            border = androidx.compose.foundation.BorderStroke(1.dp, MaterialTheme.colorScheme.outline)
                        ) {
                            Column(modifier = Modifier.padding(14.dp)) {
                                Text(
                                    text = "Scanned Ingredient Text",
                                    style = MaterialTheme.typography.titleSmall,
                                    fontWeight = FontWeight.SemiBold,
                                    color = MaterialTheme.colorScheme.onSurface
                                )
                                Spacer(modifier = Modifier.height(6.dp))
                                Text(
                                    text = product.rawIngredientText,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    lineHeight = 18.sp
                                )
                            }
                        }
                    }
                }

                // Source Footnote
                item {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.Center
                    ) {
                        Icon(
                            imageVector = if (analysis.isFromDatabaseCache) Icons.Default.Storage else Icons.Default.Refresh,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                            modifier = Modifier.size(14.dp)
                        )
                        Spacer(modifier = Modifier.width(6.dp))
                        Text(
                            text = if (analysis.isFromDatabaseCache)
                                "Verified from Food Safety Database"
                            else
                                "Analyzed via NutriGuard Scientific Engine",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f)
                        )
                    }
                }

                item {
                    Spacer(modifier = Modifier.height(24.dp))
                }
            }

            // Scientific Detail Bottom Sheet Modal
            IngredientDetailBottomSheet(
                ingredient = selectedIngredientForDetail,
                onDismiss = { selectedIngredientForDetail = null }
            )
        }

        else -> {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center
            ) {
                Text("No analysis available.")
            }
        }
    }
    }
}

internal fun isEnrichableProductBarcode(barcode: String): Boolean {
    val normalized = barcode.trim()
    return normalized.all(Char::isDigit) && normalized.length in setOf(8, 12, 13, 14)
}

internal fun productEnrichmentStatusText(
    state: BarcodeLookupUiState,
    isForThisProduct: Boolean
): String = when {
    state is BarcodeLookupUiState.Searching && isForThisProduct ->
        "The label is being combined with this barcode product."
    state is BarcodeLookupUiState.LabelScanRequired && isForThisProduct -> when {
        state.nutritionScanRequired == true && state.ingredientsScanRequired == true ->
            "Saved to this product. Scan another side showing both the ingredient list and nutrition table."
        state.nutritionScanRequired == true ->
            "Ingredients were saved to this product. Scan the nutrition table to complete it."
        state.ingredientsScanRequired == true ->
            "Nutrition was saved to this product. Scan the ingredient list to complete it."
        else ->
            "The recognized information was saved to this product. You can scan another label section."
    }
    state is BarcodeLookupUiState.Failed && isForThisProduct -> state.message
    else -> "Photograph the ingredient list or nutrition table. You can repeat this for another side of the package."
}

@Composable
private fun RiskSummaryPill(label: String, color: Color) {
    Row(
        modifier = Modifier
            .clip(RoundedCornerShape(NutriGuardRadius.pill))
            .background(Color.White.copy(alpha = 0.72f))
            .border(
                1.dp,
                color.copy(alpha = 0.20f),
                RoundedCornerShape(NutriGuardRadius.pill)
            )
            .padding(horizontal = 10.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(6.dp)
                .clip(CircleShape)
                .background(color)
        )
        Spacer(modifier = Modifier.width(6.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Medium,
            color = color
        )
    }
}

@Composable
private fun ProductIngredientRow(
    ingredient: IngredientEntity,
    onClick: () -> Unit
) {
    val riskColor = when (ingredient.riskLevel) {
        RiskLevel.SAFE -> RiskGreen
        RiskLevel.MODERATE -> RiskYellow
        RiskLevel.POTENTIAL_CONCERN -> RiskOrange
        RiskLevel.HIGH_CONCERN -> RiskRed
    }
    val riskLabel = when (ingredient.riskLevel) {
        RiskLevel.SAFE -> "LOW CONCERN"
        RiskLevel.MODERATE -> "MODERATE"
        RiskLevel.POTENTIAL_CONCERN -> "POTENTIAL"
        RiskLevel.HIGH_CONCERN -> "HIGH CONCERN"
    }
    val secondaryName = ingredient.scientificName.cleanOrNull()
        ?: ingredient.eNumber.cleanOrNull()
        ?: ingredient.category.cleanOrNull()
    val explanation = ingredient.description.cleanOrNull()
        ?: ingredient.purposeInFood.cleanOrNull()
        ?: "Open the scientific profile for more information."

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 13.dp),
        verticalAlignment = Alignment.Top
    ) {
        Box(
            modifier = Modifier
                .padding(top = 6.dp)
                .size(8.dp)
                .clip(CircleShape)
                .background(riskColor)
        )
        Spacer(modifier = Modifier.width(12.dp))
        Column(modifier = Modifier.weight(1f)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = ingredient.commonName,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                    color = ScannerSlatePrimary
                )
                Spacer(modifier = Modifier.width(8.dp))
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(NutriGuardRadius.pill))
                        .background(riskColor.copy(alpha = 0.08f))
                        .border(
                            1.dp,
                            riskColor.copy(alpha = 0.30f),
                            RoundedCornerShape(NutriGuardRadius.pill)
                        )
                        .padding(horizontal = 8.dp, vertical = 3.dp)
                ) {
                    Text(
                        text = riskLabel,
                        fontSize = 9.sp,
                        fontWeight = FontWeight.Medium,
                        color = riskColor
                    )
                }
            }

            secondaryName?.let {
                Spacer(modifier = Modifier.height(2.dp))
                Text(
                    text = it,
                    style = MaterialTheme.typography.labelSmall,
                    color = ScannerSlateMuted
                )
            }

            Spacer(modifier = Modifier.height(5.dp))
            Text(
                text = explanation,
                style = MaterialTheme.typography.bodySmall,
                color = ScannerSlateSecondary,
                lineHeight = 18.sp
            )
        }
    }
}

