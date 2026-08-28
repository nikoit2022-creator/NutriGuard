package com.example.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.data.model.ProductEntity
import com.example.ui.theme.NutriGuardRadius
import com.example.ui.theme.RiskGreen
import com.example.ui.theme.RiskGreenBgDark
import com.example.ui.theme.RiskGreenBgLight

@Composable
fun DietaryBadgesRow(
    product: ProductEntity,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        BadgeItem(label = "Gluten-Free", isCompliant = product.isGlutenFree)
        Spacer(modifier = Modifier.width(8.dp))
        BadgeItem(label = "Lactose-Free", isCompliant = product.isLactoseFree)
        Spacer(modifier = Modifier.width(8.dp))
        BadgeItem(label = "Vegan", isCompliant = product.isVegan)
        Spacer(modifier = Modifier.width(8.dp))
        BadgeItem(label = "Vegetarian", isCompliant = product.isVegetarian)
        Spacer(modifier = Modifier.width(8.dp))
        BadgeItem(label = "Halal", isCompliant = product.isHalal)
        Spacer(modifier = Modifier.width(8.dp))
        BadgeItem(label = "Kosher", isCompliant = product.isKosher)
    }
}

@Composable
private fun BadgeItem(label: String, isCompliant: Boolean) {
    val isDark = isSystemInDarkTheme()

    val bg = if (isCompliant) {
        if (isDark) RiskGreenBgDark else RiskGreenBgLight
    } else {
        if (isDark) Color(0xFF1E293B) else Color(0xFFF1F5F9)
    }

    val fg = if (isCompliant) {
        if (isDark) Color(0xFF86EFAC) else Color(0xFF15803D)
    } else {
        if (isDark) Color(0xFF94A3B8) else Color(0xFF64748B)
    }

    val borderColor = if (isCompliant) {
        RiskGreen.copy(alpha = 0.35f)
    } else {
        MaterialTheme.colorScheme.outline
    }

    val symbol = if (isCompliant) "✓" else "✕"

    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(NutriGuardRadius.small))
            .background(bg)
            .border(1.dp, borderColor, RoundedCornerShape(NutriGuardRadius.small))
            .padding(horizontal = 10.dp, vertical = 6.dp)
    ) {
        Text(
            text = "$symbol $label",
            fontSize = 12.sp,
            fontWeight = if (isCompliant) FontWeight.SemiBold else FontWeight.Normal,
            color = fg
        )
    }
}

