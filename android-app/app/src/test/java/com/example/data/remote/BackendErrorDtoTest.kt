package com.example.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Covers the required "structured error parsing" behavior: the backend
 * error envelope (`error.code`/`error.message`/`error.details.*`) must
 * parse tolerantly -- missing fields, malformed bodies, and literal
 * "null"/"None"/blank placeholder text must never crash or leak
 * through to display.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [36])
class BackendErrorDtoTest {

    @Test
    fun `fromJson parses a complete labelScanRequired envelope`() {
        val body = """
            {
              "error": {
                "code": "PRODUCT_NOT_FOUND",
                "message": "No product found for barcode 4006381333931.",
                "details": {
                  "labelScanRequired": true,
                  "reason": "This product's identity was found, but its nutrition and/or ingredient data is too incomplete for a reliable Health Score.",
                  "discoveredIdentity": {
                    "barcode": "4006381333931",
                    "productName": "Diagnostic Test Product",
                    "brand": "DiagnosticTest",
                    "imageUrl": "https://images.example.org/x.jpg"
                  },
                  "providersChecked": [
                    {"provider": "open_food_facts", "outcome": "found"},
                    {"provider": "gs1_digital_link", "outcome": "not_found"},
                    {"provider": "upcitemdb", "outcome": "skipped"}
                  ],
                  "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly."
                },
                "timestamp": 1788165535157
              }
            }
        """.trimIndent()

        val dto = BackendErrorDto.fromJson(body)

        assertEquals("PRODUCT_NOT_FOUND", dto?.code)
        assertEquals("No product found for barcode 4006381333931.", dto?.message)
        val details = dto?.details
        assertTrue(details?.labelScanRequired == true)
        assertEquals(
            "This product's identity was found, but its nutrition and/or ingredient data is too incomplete for a reliable Health Score.",
            details?.reason
        )
        assertEquals(
            "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly.",
            details?.suggestedAction
        )
        assertEquals(listOf("open_food_facts", "gs1_digital_link", "upcitemdb"), details?.providersChecked)

        val identity = details?.discoveredIdentity
        assertEquals("4006381333931", identity?.barcode)
        assertEquals("Diagnostic Test Product", identity?.productName)
        assertEquals("DiagnosticTest", identity?.brand)
        assertEquals("https://images.example.org/x.jpg", identity?.imageUrl)
    }

    @Test
    fun `fromJson parses a plain not-found envelope with no discoveredIdentity`() {
        val body = """
            {
              "error": {
                "code": "PRODUCT_NOT_FOUND",
                "message": "No product found for barcode 0000000000000.",
                "details": {
                  "labelScanRequired": true,
                  "reason": "Barcode not found in the local database or any configured external source.",
                  "providersChecked": [
                    {"provider": "open_food_facts", "outcome": "not_found"}
                  ],
                  "suggestedAction": "Use POST /scan/label-image or POST /scan/ocr-text to analyze the product's label directly."
                },
                "timestamp": 1788165535157
              }
            }
        """.trimIndent()

        val dto = BackendErrorDto.fromJson(body)

        assertNull(dto?.details?.discoveredIdentity)
        assertEquals(listOf("open_food_facts"), dto?.details?.providersChecked)
    }

    @Test
    fun `fromJson never throws on malformed or unrelated JSON`() {
        assertNull(BackendErrorDto.fromJson("not json at all"))
        assertNull(BackendErrorDto.fromJson(""))
        assertNull(BackendErrorDto.fromJson("{}"))
        assertNull(BackendErrorDto.fromJson("""{"detail": "some other unrelated error shape"}"""))
        assertNull(BackendErrorDto.fromJson("""{"error": "not an object"}"""))
    }

    @Test
    fun `fromJson tolerates a details object missing every optional field`() {
        val body = """{"error": {"code": "PRODUCT_NOT_FOUND", "details": {}}}"""
        val dto = BackendErrorDto.fromJson(body)

        assertFalse(dto?.details?.labelScanRequired ?: true)
        assertNull(dto?.details?.reason)
        assertNull(dto?.details?.suggestedAction)
        assertNull(dto?.details?.discoveredIdentity)
        assertTrue(dto?.details?.providersChecked?.isEmpty() == true)
    }

    @Test
    fun `literal null and None placeholders in discoveredIdentity are filtered out`() {
        val body = """
            {
              "error": {
                "code": "PRODUCT_NOT_FOUND",
                "details": {
                  "labelScanRequired": true,
                  "reason": "null",
                  "suggestedAction": "None",
                  "discoveredIdentity": {
                    "barcode": "4006381333931",
                    "productName": "Real Product Name",
                    "brand": "None",
                    "imageUrl": ""
                  }
                }
              }
            }
        """.trimIndent()

        val dto = BackendErrorDto.fromJson(body)

        assertNull("literal 'null' string must not be shown as a reason", dto?.details?.reason)
        assertNull("literal 'None' string must not be shown as a suggestedAction", dto?.details?.suggestedAction)
        // productName is real, so the identity survives as non-empty --
        // its OTHER placeholder/blank fields must still be filtered
        // individually, not leak through just because the object as a
        // whole wasn't discarded.
        val identity = dto?.details?.discoveredIdentity
        assertEquals("Real Product Name", identity?.productName)
        assertNull("literal 'None' string must not be shown as a brand", identity?.brand)
        assertNull("a blank imageUrl must not be shown", identity?.imageUrl)
    }

    @Test
    fun `a discoveredIdentity with no safe fields at all is treated as absent`() {
        val body = """
            {
              "error": {
                "code": "PRODUCT_NOT_FOUND",
                "details": {
                  "labelScanRequired": true,
                  "discoveredIdentity": {
                    "barcode": "4006381333931",
                    "productName": "null",
                    "brand": "undefined",
                    "imageUrl": "N/A"
                  }
                }
              }
            }
        """.trimIndent()

        val dto = BackendErrorDto.fromJson(body)
        // barcode alone doesn't make an identity "worth showing" -- see
        // DiscoveredIdentity.isEmpty (productName/brand/imageUrl all placeholders).
        assertNull(dto?.details?.discoveredIdentity)
    }

    @Test
    fun `a 404 that is not the labelScanRequired shape is still parseable but not flagged`() {
        val body = """{"error": {"code": "VALIDATION_ERROR", "message": "barcode must not be empty."}}"""
        val dto = BackendErrorDto.fromJson(body)

        assertEquals("VALIDATION_ERROR", dto?.code)
        assertNull(dto?.details)
    }

    @Test
    fun `malformed providersChecked entries are skipped without crashing`() {
        val body = """
            {
              "error": {
                "code": "PRODUCT_NOT_FOUND",
                "details": {
                  "labelScanRequired": true,
                  "providersChecked": [
                    {"provider": "open_food_facts", "outcome": "found"},
                    "not an object",
                    {"outcome": "not_found"},
                    null,
                    {"provider": "", "outcome": "skipped"}
                  ]
                }
              }
            }
        """.trimIndent()

        val dto = BackendErrorDto.fromJson(body)
        assertEquals(listOf("open_food_facts"), dto?.details?.providersChecked)
    }
}
