package com.example.data.diagnostics

import androidx.sqlite.db.SupportSQLiteOpenHelper
import androidx.sqlite.db.SupportSQLiteDatabase
import androidx.sqlite.db.framework.FrameworkSQLiteOpenHelperFactory
import androidx.test.core.app.ApplicationProvider
import com.example.data.db.AppDatabase
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

@RunWith(RobolectricTestRunner::class)
class ScanHistoryMigrationTest {
    @Test fun upgradePreservesHistoryAndDoesNotInventLegacyIds() {
        val helper = FrameworkSQLiteOpenHelperFactory().create(
            SupportSQLiteOpenHelper.Configuration.builder(ApplicationProvider.getApplicationContext())
                .callback(object : SupportSQLiteOpenHelper.Callback(5) {
                    override fun onCreate(db: SupportSQLiteDatabase) {}
                    override fun onUpgrade(db: SupportSQLiteDatabase, oldVersion: Int, newVersion: Int) {}
                }).build()
        )
        try {
            val db = helper.writableDatabase
            db.execSQL("CREATE TABLE scan_history (id INTEGER NOT NULL PRIMARY KEY, barcode TEXT, productName TEXT NOT NULL, brand TEXT NOT NULL, healthScore INTEGER NOT NULL, scannedAt INTEGER NOT NULL, scanType TEXT NOT NULL)")
            db.execSQL("INSERT INTO scan_history VALUES (1, '40144474', 'Existing', 'Brand', 50, 1000, 'BARCODE')")
            AppDatabase.MIGRATION_5_6.migrate(db)
            db.query("SELECT productName, scanAttemptId FROM scan_history WHERE id=1").use {
                assertTrue(it.moveToFirst())
                assertEquals("Existing", it.getString(0))
                assertTrue(it.isNull(1))
            }
            db.execSQL("INSERT INTO scan_attempts VALUES ('0000123456789012', 'LABEL_CAMERA', 1000, 'CANCELLED', NULL)")
            db.query("SELECT scanAttemptId FROM scan_attempts").use {
                assertTrue(it.moveToFirst())
                assertEquals("0000123456789012", it.getString(0))
            }
        } finally { helper.close() }
    }
}
