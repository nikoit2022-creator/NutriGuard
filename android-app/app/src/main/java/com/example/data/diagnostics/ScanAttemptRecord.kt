package com.example.data.diagnostics

import androidx.room.Entity
import androidx.room.PrimaryKey
import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Transaction
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "scan_attempts")
data class ScanAttemptRecord(
    @PrimaryKey val scanAttemptId: String,
    val input: String,
    val startedAt: Long,
    val outcome: String,
    val serverAttemptId: String? = null
)

@Dao
abstract class ScanAttemptDao {
    @Query("SELECT * FROM scan_attempts ORDER BY startedAt DESC LIMIT 200")
    abstract fun observe(): Flow<List<ScanAttemptRecord>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun insert(record: ScanAttemptRecord)

    @Query("DELETE FROM scan_attempts WHERE scanAttemptId NOT IN (SELECT scanAttemptId FROM scan_attempts ORDER BY startedAt DESC LIMIT 200)")
    abstract suspend fun prune()

    @Transaction
    open suspend fun save(record: ScanAttemptRecord) { insert(record); prune() }
}
