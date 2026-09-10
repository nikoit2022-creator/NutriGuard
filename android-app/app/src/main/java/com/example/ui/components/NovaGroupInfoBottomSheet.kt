package com.example.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Info
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskOrange
import com.example.ui.theme.RiskRed
import com.example.ui.theme.RiskYellow

data class NovaGroupUiInfo(
    val group: Int,
    val title: String,
    val description: String,
    val color: Color
)

fun novaGroupUiInfo(group: Int): NovaGroupUiInfo? = when (group) {
    1 -> NovaGroupUiInfo(1, "Unprocessed or minimally processed", "Foods kept close to their natural state, with processing such as cleaning, freezing or pasteurizing.", RiskGreen)
    2 -> NovaGroupUiInfo(2, "Processed culinary ingredients", "Ingredients obtained from Group 1 foods or nature and mainly used for cooking, such as oils, butter, sugar and salt.", RiskYellow)
    3 -> NovaGroupUiInfo(3, "Processed foods", "Group 1 foods combined with Group 2 ingredients, usually to improve preservation or taste.", RiskOrange)
    4 -> NovaGroupUiInfo(4, "Ultra-processed foods", "Industrial formulations that commonly contain several ingredients, additives or processes not normally used in home cooking.", RiskRed)
    else -> null
}

private val novaGroups = (1..4).mapNotNull(::novaGroupUiInfo)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun NovaGroupInfoBottomSheet(
    selectedGroup: Int?,
    onDismiss: () -> Unit
) {
    if (selectedGroup == null) return
    val selected = novaGroupUiInfo(selectedGroup)
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = MaterialTheme.colorScheme.surface
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp, vertical = 8.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(38.dp)
                            .clip(CircleShape)
                            .background(MaterialTheme.colorScheme.primaryContainer),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(Icons.Default.Info, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                    }
                    Text(
                        text = "NOVA food processing",
                        modifier = Modifier.padding(start = 10.dp),
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold
                    )
                }
                IconButton(onClick = onDismiss) {
                    Icon(Icons.Default.Close, contentDescription = "Close")
                }
            }

            if (selected != null) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(NutriGuardRadius.medium))
                        .background(selected.color.copy(alpha = 0.10f))
                        .border(1.dp, selected.color.copy(alpha = 0.35f), RoundedCornerShape(NutriGuardRadius.medium))
                        .padding(16.dp)
                ) {
                    Text("This product is Group ${selected.group}", fontWeight = FontWeight.Bold, color = selected.color)
                    Spacer(Modifier.height(4.dp))
                    Text(selected.title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Spacer(Modifier.height(4.dp))
                    Text(selected.description, style = MaterialTheme.typography.bodyMedium)
                }
            }

            Text("What the four groups mean", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
            novaGroups.forEach { info ->
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(NutriGuardRadius.small))
                        .background(if (info.group == selectedGroup) info.color.copy(alpha = 0.08f) else MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
                        .padding(12.dp),
                    verticalAlignment = Alignment.Top
                ) {
                    Box(
                        modifier = Modifier
                            .size(28.dp)
                            .clip(CircleShape)
                            .background(info.color),
                        contentAlignment = Alignment.Center
                    ) {
                        Text("${info.group}", color = Color.White, fontWeight = FontWeight.Bold)
                    }
                    Column(modifier = Modifier.padding(start = 10.dp)) {
                        Text(info.title, fontWeight = FontWeight.SemiBold)
                        Spacer(Modifier.height(2.dp))
                        Text(info.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }

            Text(
                text = "NOVA describes how and why a food was processed. It does not replace the nutrition facts, ingredient safety information or the overall Health Score.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            Spacer(Modifier.height(24.dp))
        }
    }
}
