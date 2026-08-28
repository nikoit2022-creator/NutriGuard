package com.example.data.model

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "scan_history")
data class ScanHistoryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val barcode: String?,
    val productName: String,
    val brand: String,
    val healthScore: Int,
    val scannedAt: Long = System.currentTimeMillis(),
    val scanType: String // "BARCODE", "OCR_LABEL", "MANUAL_INPUT"
)
