package com.example.data.remote

import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.room.Room
import com.example.data.db.AppDatabase
import com.example.data.remote.dto.IngredientDto
import com.example.data.remote.dto.toEntities
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class OpenFoodToxMigrationTest {
    @Test fun roomSchemaMigration() = runBlocking {
        val context = RuntimeEnvironment.getApplication()
        // Short name avoids native SQLite MAX_PATH failures on Windows test hosts.
        val name = "pilot.db"
        var room = Room.databaseBuilder(context, AppDatabase::class.java, name).allowMainThreadQueries().build()
        try {
            val json = JSONObject(requireNotNull(javaClass.classLoader?.getResourceAsStream("e951_ingredient_out.json"))
                .bufferedReader().use { it.readText() })
            val entity = listOf(IngredientDto.fromJson(json)).toEntities("migration").single()
            room.ingredientDao().insertIngredient(entity)
            val db = room.openHelper.writableDatabase
            val createSql = db.query("SELECT sql FROM sqlite_master WHERE name='ingredients'").use {
                assertTrue(it.moveToFirst()); it.getString(0)
            }
            var oldSql = createSql
            listOf("effectConditions", "dietaryGuidance", "adiPopulationScope").forEach {
                oldSql = oldSql.replace(", `$it` TEXT", "")
            }
            assertNotEquals(createSql, oldSql)
            val columns = db.query("PRAGMA table_info(ingredients)").use { cursor ->
                buildList {
                    while (cursor.moveToNext()) {
                        val column = cursor.getString(cursor.getColumnIndexOrThrow("name"))
                        if (column !in listOf("effectConditions", "dietaryGuidance", "adiPopulationScope")) add("`$column`")
                    }
                }.joinToString(",")
            }
            db.execSQL("ALTER TABLE ingredients RENAME TO saved_ingredients")
            db.execSQL(oldSql)
            db.execSQL("INSERT INTO ingredients ($columns) SELECT $columns FROM saved_ingredients")
            db.execSQL("DROP TABLE saved_ingredients")
            db.execSQL("DELETE FROM room_master_table")
            db.version = 4
            room.close()
            room = Room.databaseBuilder(context, AppDatabase::class.java, name)
                .addMigrations(AppDatabase.MIGRATION_4_5).allowMainThreadQueries().build()
            val restored = requireNotNull(room.ingredientDao().getIngredientByIdOrEnum(entity.id))
            assertEquals(entity.description, restored.description)
            assertEquals(entity.localizationsJson, restored.localizationsJson)
            assertNull(restored.effectConditions)
            assertNull(restored.dietaryGuidance)
            assertNull(restored.adiPopulationScope)
        } finally {
            room.close()
            context.deleteDatabase(name)
        }
    }
    @Test fun `additive migration preserves cached ingredient and unrelated data`() {
        val helper = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(RuntimeEnvironment.getApplication())
                .name(null)
                .callback(object : SupportSQLiteOpenHelper.Callback(4) {
                    override fun onCreate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                        db.execSQL("CREATE TABLE ingredients (id TEXT PRIMARY KEY, description TEXT NOT NULL, localizationsJson TEXT NOT NULL)")
                        db.execSQL("INSERT INTO ingredients VALUES ('e951', 'Existing description', '{}')")
                        db.execSQL("CREATE TABLE products (barcode TEXT PRIMARY KEY)")
                        db.execSQL("INSERT INTO products VALUES ('12345')")
                    }
                    override fun onUpgrade(db: androidx.sqlite.db.SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
                }).build()
        )
        try {
            val db = helper.writableDatabase
            AppDatabase.MIGRATION_4_5.migrate(db)
            db.query("SELECT * FROM ingredients").use { cursor ->
                assertTrue(cursor.moveToFirst())
                assertEquals("Existing description", cursor.getString(cursor.getColumnIndexOrThrow("description")))
                listOf("effectConditions", "dietaryGuidance", "adiPopulationScope").forEach {
                    assertTrue(cursor.isNull(cursor.getColumnIndexOrThrow(it)))
                }
            }
            db.query("SELECT barcode FROM products").use { cursor ->
                assertTrue(cursor.moveToFirst())
                assertEquals("12345", cursor.getString(0))
            }
        } finally { helper.close() }
    }
}
