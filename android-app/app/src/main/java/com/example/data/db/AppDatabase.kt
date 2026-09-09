package com.example.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.example.data.dao.IngredientDao
import com.example.data.dao.ProductDao
import com.example.data.dao.ScanHistoryDao
import com.example.data.dao.UserHealthProfileDao
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.ScanHistoryEntity
import com.example.data.model.UserHealthProfile
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

@Database(
    entities = [
        IngredientEntity::class,
        ProductEntity::class,
        UserHealthProfile::class,
        ScanHistoryEntity::class
    ],
    version = 3,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun ingredientDao(): IngredientDao
    abstract fun productDao(): ProductDao
    abstract fun userHealthProfileDao(): UserHealthProfileDao
    abstract fun scanHistoryDao(): ScanHistoryDao

    companion object {
        private val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Rebuild only the ingredient catalog table so the six
                // dietary flags can preserve backend `null` (unknown).
                // Product history/profile tables remain untouched.
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `ingredients_new` (
                        `id` TEXT NOT NULL,
                        `commonName` TEXT NOT NULL,
                        `scientificName` TEXT NOT NULL,
                        `eNumber` TEXT,
                        `category` TEXT NOT NULL,
                        `description` TEXT NOT NULL,
                        `purposeInFood` TEXT NOT NULL,
                        `healthConcerns` TEXT NOT NULL,
                        `evidenceLevel` TEXT NOT NULL,
                        `countriesRestrictedOrBanned` TEXT NOT NULL,
                        `efsaStatus` TEXT NOT NULL,
                        `fdaStatus` TEXT NOT NULL,
                        `whoIarcClassification` TEXT,
                        `acceptableDailyIntake` TEXT NOT NULL,
                        `sideEffects` TEXT NOT NULL,
                        `allergens` TEXT NOT NULL,
                        `references` TEXT NOT NULL,
                        `riskLevel` TEXT NOT NULL,
                        `riskAssessmentAvailable` INTEGER NOT NULL,
                        `riskRationale` TEXT,
                        `efsaApprovalStatus` TEXT,
                        `fdaApprovalStatus` TEXT,
                        `adiMinMgPerKgBwPerDay` REAL,
                        `adiMaxMgPerKgBwPerDay` REAL,
                        `adiSource` TEXT,
                        `sourceUrl` TEXT,
                        `isGluten` INTEGER,
                        `isLactose` INTEGER,
                        `isVegan` INTEGER,
                        `isVegetarian` INTEGER,
                        `isHalal` INTEGER,
                        `isKosher` INTEGER,
                        `badForDiabetes` INTEGER NOT NULL,
                        `badForHypertension` INTEGER NOT NULL,
                        `badForKidneyDisease` INTEGER NOT NULL,
                        `badForGout` INTEGER NOT NULL,
                        `badForPregnancy` INTEGER NOT NULL,
                        `badForChildren` INTEGER NOT NULL,
                        `badForHighCholesterol` INTEGER NOT NULL,
                        PRIMARY KEY(`id`)
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    INSERT INTO `ingredients_new` (
                        `id`, `commonName`, `scientificName`, `eNumber`, `category`,
                        `description`, `purposeInFood`, `healthConcerns`, `evidenceLevel`,
                        `countriesRestrictedOrBanned`, `efsaStatus`, `fdaStatus`,
                        `whoIarcClassification`, `acceptableDailyIntake`, `sideEffects`,
                        `allergens`, `references`, `riskLevel`, `riskAssessmentAvailable`,
                        `isGluten`, `isLactose`, `isVegan`, `isVegetarian`, `isHalal`,
                        `isKosher`, `badForDiabetes`, `badForHypertension`,
                        `badForKidneyDisease`, `badForGout`, `badForPregnancy`,
                        `badForChildren`, `badForHighCholesterol`
                    )
                    SELECT
                        `id`, `commonName`, `scientificName`, `eNumber`, `category`,
                        `description`, `purposeInFood`, `healthConcerns`, `evidenceLevel`,
                        `countriesRestrictedOrBanned`, `efsaStatus`, `fdaStatus`,
                        `whoIarcClassification`, `acceptableDailyIntake`, `sideEffects`,
                        `allergens`, `references`, `riskLevel`, 1,
                        `isGluten`, `isLactose`, `isVegan`, `isVegetarian`, `isHalal`,
                        `isKosher`, `badForDiabetes`, `badForHypertension`,
                        `badForKidneyDisease`, `badForGout`, `badForPregnancy`,
                        `badForChildren`, `badForHighCholesterol`
                    FROM `ingredients`
                    """.trimIndent()
                )
                db.execSQL("DROP TABLE `ingredients`")
                db.execSQL("ALTER TABLE `ingredients_new` RENAME TO `ingredients`")
            }
        }

        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "nutriguard_db"
                )
                    .addMigrations(MIGRATION_2_3)
                    .addCallback(object : Callback() {
                        override fun onCreate(db: SupportSQLiteDatabase) {
                            super.onCreate(db)
                            CoroutineScope(Dispatchers.IO).launch {
                                val dbInstance = getDatabase(context)
                                // Pre-seed Scientific Ingredient Database
                                dbInstance.ingredientDao().insertAll(InitialScientificData.INGREDIENTS)
                                // Pre-seed Sample Products
                                InitialScientificData.PRODUCTS.forEach { prod ->
                                    dbInstance.productDao().insertProduct(prod)
                                }
                                // Default Health Profile
                                dbInstance.userHealthProfileDao().saveProfile(UserHealthProfile())
                            }
                        }
                    })
                    .fallbackToDestructiveMigration()
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
