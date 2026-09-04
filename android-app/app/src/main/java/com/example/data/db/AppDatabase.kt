package com.example.data.db

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
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
    version = 2,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {

    abstract fun ingredientDao(): IngredientDao
    abstract fun productDao(): ProductDao
    abstract fun userHealthProfileDao(): UserHealthProfileDao
    abstract fun scanHistoryDao(): ScanHistoryDao

    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null

        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "nutriguard_db"
                )
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
