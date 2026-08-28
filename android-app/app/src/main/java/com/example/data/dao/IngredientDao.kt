package com.example.data.dao

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import com.example.data.model.IngredientEntity
import com.example.data.model.RiskLevel
import kotlinx.coroutines.flow.Flow

@Dao
interface IngredientDao {
    @Query("SELECT * FROM ingredients ORDER BY commonName ASC")
    fun getAllIngredients(): Flow<List<IngredientEntity>>

    @Query("SELECT * FROM ingredients WHERE id = :id OR eNumber = :id LIMIT 1")
    suspend fun getIngredientByIdOrEnum(id: String): IngredientEntity?

    @Query("SELECT * FROM ingredients WHERE commonName LIKE '%' || :query || '%' OR scientificName LIKE '%' || :query || '%' OR eNumber LIKE '%' || :query || '%'")
    fun searchIngredients(query: String): Flow<List<IngredientEntity>>

    @Query("SELECT * FROM ingredients WHERE riskLevel = :riskLevel")
    fun getIngredientsByRisk(riskLevel: RiskLevel): Flow<List<IngredientEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertIngredient(ingredient: IngredientEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(ingredients: List<IngredientEntity>)

    @Query("SELECT COUNT(*) FROM ingredients")
    suspend fun getIngredientCount(): Int
}
