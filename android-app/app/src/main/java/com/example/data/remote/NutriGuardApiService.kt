package com.example.data.remote

import android.graphics.Bitmap
import android.util.Log
import com.example.BuildConfig
import com.example.data.remote.dto.ParsedScanData
import com.example.data.remote.dto.ScanLabelImageResponseDto
import com.example.data.remote.dto.toParsedEntities
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

class NutriGuardApiService(
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
) {
    private val baseUrl: String = BuildConfig.BACKEND_BASE_URL.trimEnd('/')

    // Barcode discovery may query several external providers sequentially
    // server-side (Open Food Facts, GS1, UPCitemdb) before answering, so
    // it needs materially more headroom than a typical request -- but
    // this must never shorten the base client's timeouts (60s), which
    // image upload also relies on. `callTimeout` bounds the WHOLE
    // request (connect + write + server processing + read), which is
    // exactly what "give the backend flow enough time end-to-end" means
    // here; connect/read/write on the derived client stay inherited
    // from `httpClient`, only capped tighter by the overall call bound.
    private val barcodeHttpClient: OkHttpClient by lazy {
        httpClient.newBuilder()
            .callTimeout(BARCODE_CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .build()
    }

    /**
     * Sends a scanned/typed barcode to the NutriGuard FastAPI backend endpoint:
     * POST /api/v1/scan/barcode
     *
     * On success, returns the same [ParsedScanData] shape [scanLabelImage]
     * does (the backend uses one contract, `FullProductAnalysisOut`, for
     * both). On a structured "not found / label scan required" response
     * throws [LabelScanRequiredException]; on any other failure throws a
     * more specific [BarcodeScanException] subtype so callers can react
     * to *why* it failed instead of treating every error alike.
     */
    suspend fun scanBarcode(barcode: String): ParsedScanData = withContext(Dispatchers.IO) {
        val jsonPayload = JSONObject().apply { put("barcode", barcode) }
        val endpointUrl = "$baseUrl/api/v1/scan/barcode"
        Log.d(TAG, "Executing scanBarcode POST request to: $endpointUrl")

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .build()

        val response = try {
            barcodeHttpClient.newCall(request).execute()
        } catch (e: SocketTimeoutException) {
            Log.w(TAG, "Barcode lookup timed out calling $endpointUrl")
            throw BarcodeTimeoutException(
                "The product lookup is taking too long. Please check your connection and try again.",
                e
            )
        } catch (e: IOException) {
            Log.e(TAG, "Network connection failure to $endpointUrl: ${e.localizedMessage}", e)
            throw BarcodeNetworkException(
                "Unable to reach the NutriGuard server. Check your network connection and try again.",
                e
            )
        }

        response.use { resp ->
            val responseBody = resp.body?.string() ?: ""

            if (!resp.isSuccessful) {
                if (resp.code == 404) {
                    val backendError = BackendErrorDto.fromJson(responseBody)
                    val details = backendError?.details
                    if (backendError?.code == "PRODUCT_NOT_FOUND" && details?.labelScanRequired == true) {
                        throw LabelScanRequiredException(
                            reason = details.reason
                                ?: "This product could not be fully identified from its barcode.",
                            suggestedAction = details.suggestedAction,
                            providersChecked = details.providersChecked,
                            discoveredIdentity = details.discoveredIdentity
                        )
                    }
                }
                if (resp.code == 401 || resp.code == 403) {
                    // AuthInterceptor already retried once with a refreshed/
                    // re-authenticated token (see its docs); reaching here
                    // means that also failed, so the device genuinely
                    // couldn't be authenticated -- not just "a server error".
                    Log.e(TAG, "NutriGuard Backend authentication failure HTTP ${resp.code}")
                    throw BarcodeAuthException(
                        resp.code,
                        "Your session could not be verified. Please restart the app and try again."
                    )
                }
                Log.e(TAG, "NutriGuard Backend error HTTP ${resp.code}")
                throw BarcodeServerException(
                    resp.code,
                    "NutriGuard backend returned an unexpected error (HTTP ${resp.code}). Please try again."
                )
            }

            try {
                val jsonObject = JSONObject(responseBody)
                val dto = ScanLabelImageResponseDto.fromJson(jsonObject)
                return@withContext dto.toParsedEntities()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to parse backend barcode response JSON: ${e.localizedMessage}", e)
                throw BarcodeParseException("Received an unexpected response from the server.", e)
            }
        }
    }

    /**
     * Sends an image bitmap to the NutriGuard FastAPI backend endpoint:
     * POST /api/v1/scan/label-image
     */
    suspend fun scanLabelImage(bitmap: Bitmap): ParsedScanData = withContext(Dispatchers.IO) {
        val stream = ByteArrayOutputStream()
        val compressSuccess = bitmap.compress(Bitmap.CompressFormat.JPEG, 80, stream)
        if (!compressSuccess) {
            throw IOException("Failed to compress image bitmap to JPEG format")
        }
        val byteArray = stream.toByteArray()

        val requestBody = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addPart(buildLabelImagePart(byteArray))
            .build()

        val endpointUrl = "$baseUrl/api/v1/scan/label-image"
        Log.d(TAG, "Executing scanLabelImage POST request to: $endpointUrl (payload size: ${byteArray.size} bytes)")

        val request = Request.Builder()
            .url(endpointUrl)
            .post(requestBody)
            .header("Accept", "application/json")
            .build()

        val response = try {
            httpClient.newCall(request).execute()
        } catch (e: IOException) {
            Log.e(TAG, "Network connection failure to $endpointUrl: ${e.localizedMessage}", e)
            throw IOException("Backend connection failed: Unable to reach NutriGuard server at $baseUrl. Check network and backend status.")
        }

        response.use { resp ->
            val responseBody = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                Log.e(TAG, "NutriGuard Backend error HTTP ${resp.code}: $responseBody")
                throw IOException("NutriGuard Backend error (HTTP ${resp.code}): ${responseBody.take(200)}")
            }

            try {
                val jsonObject = JSONObject(responseBody)
                val dto = ScanLabelImageResponseDto.fromJson(jsonObject)
                return@withContext dto.toParsedEntities()
            } catch (e: Exception) {
                Log.e(TAG, "Failed to parse backend response JSON: ${e.localizedMessage}. Response was: $responseBody", e)
                throw IOException("Invalid response structure received from NutriGuard backend: ${e.localizedMessage}")
            }
        }
    }

    companion object {
        private const val TAG = "NutriGuardApiService"

        /** Whole-request bound for POST /scan/barcode -- see [barcodeHttpClient]. */
        internal const val BARCODE_CALL_TIMEOUT_SECONDS = 30L

        /**
         * Builds the multipart form part for the label-image scan request.
         * The backend (POST /api/v1/scan/label-image) requires the form field
         * name to be "image" (see app/api/v1/scan.py::scan_label_image).
         */
        internal fun buildLabelImagePart(byteArray: ByteArray): MultipartBody.Part {
            return MultipartBody.Part.createFormData(
                name = "image",
                filename = "food_label.jpg",
                body = byteArray.toRequestBody("image/jpeg".toMediaType())
            )
        }
    }
}
