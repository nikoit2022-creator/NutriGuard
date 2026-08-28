package com.example.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.example.data.model.ScanHistoryEntity
import com.example.data.model.UserHealthProfile
import kotlinx.coroutines.flow.Flow

@Dao
interface UserHealthProfileDao {
    @Query("SELECT * FROM user_health_profile WHERE id = 1 LIMIT 1")
    fun getProfile(): Flow<UserHealthProfile?>

    @Query("SELECT * FROM user_health_profile WHERE id = 1 LIMIT 1")
    suspend fun getProfileSync(): UserHealthProfile?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun saveProfile(profile: UserHealthProfile)
}

@Dao
interface ScanHistoryDao {
    @Query("SELECT * FROM scan_history ORDER BY scannedAt DESC")
    fun getAllHistory(): Flow<List<ScanHistoryEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertHistory(history: ScanHistoryEntity)

    @Query("DELETE FROM scan_history")
    suspend fun clearHistory()
}
