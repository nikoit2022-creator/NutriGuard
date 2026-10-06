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
import kotlin.coroutines.coroutineContext
import com.example.data.diagnostics.ScanTraceContext
import com.example.data.diagnostics.LocalScanStage
import com.example.data.diagnostics.LocalScanOutcome

class NutriGuardApiService(
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
) {
    private val baseUrl: String = BuildConfig.BACKEND_BASE_URL.trimEnd('/')
    // Avoid invisible OkHttp retries with the same request sequence. Auth retry is explicit.
    private val scanHttpClient = httpClient.newBuilder().retryOnConnectionFailure(false)
        .followRedirects(false).followSslRedirects(false).build()

    private suspend fun recordResponse(response: okhttp3.Response) {
        coroutineContext[ScanTraceContext]?.let { trace ->
            trace.reason = when (response.code) {
                401 -> com.example.data.diagnostics.ScanReason.AUTH_EXPIRED
                429 -> com.example.data.diagnostics.ScanReason.RATE_LIMITED
                422 -> com.example.data.diagnostics.ScanReason.VALIDATION_REJECTED
                in 500..599 -> com.example.data.diagnostics.ScanReason.SERVER_ERROR
                else -> null
            }
            trace.serverAttemptId = response.header("X-Scan-Attempt-Id")?.let { raw ->
                runCatching { com.example.data.diagnostics.ScanAttemptId(raw) }.getOrNull()
            }
            trace.record(LocalScanStage.RESPONSE, if (response.isSuccessful) LocalScanOutcome.SUCCEEDED else LocalScanOutcome.FAILED)
        }
    }

    private suspend fun Request.Builder.withScanTrace(): Request.Builder {
        val trace = coroutineContext[ScanTraceContext] ?: return this
        val sequence = trace.nextRequest()
        trace.record(LocalScanStage.REQUEST, LocalScanOutcome.STARTED)
        return header("X-Scan-Attempt-Id", trace.id.value)
            .header("X-Scan-Request-Sequence", sequence.toString())
            .tag(ScanTraceContext::class.java, trace)
    }

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
        scanHttpClient.newBuilder()
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
            .withScanTrace()
            .build()

        val response = try {
            barcodeHttpClient.newCall(request).execute()
        } catch (e: SocketTimeoutException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_TIMEOUT
            Log.w(TAG, "Barcode lookup timed out calling $endpointUrl")
            throw BarcodeTimeoutException(
                "The product lookup is taking too long. Please check your connection and try again.",
                e
            )
        } catch (e: IOException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_OFFLINE
            Log.e(TAG, "Network connection failure to $endpointUrl: ${e.localizedMessage}", e)
            throw BarcodeNetworkException(
                "Unable to reach the NutriGuard server. Check your network connection and try again.",
                e
            )
        }

        response.use { resp ->
            recordResponse(resp)
            val responseBody = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                handleUnsuccessfulResponse(resp.code, responseBody)
            }

            try {
                val jsonObject = JSONObject(responseBody)
                val dto = ScanLabelImageResponseDto.fromJson(jsonObject)
                val parsed = dto.toParsedEntities()
                coroutineContext[ScanTraceContext]?.record?.invoke(LocalScanStage.PARSING, LocalScanOutcome.SUCCEEDED)
                return@withContext parsed
            } catch (e: Exception) {
                coroutineContext[ScanTraceContext]?.let { it.reason = com.example.data.diagnostics.ScanReason.DECODE_ERROR; it.record(LocalScanStage.PARSING, LocalScanOutcome.FAILED) }
                Log.e(TAG, "Failed to parse backend response JSON")
                throw BarcodeParseException("Received an unexpected response from the server.", e)
            }
        }
    }

    /**
     * Sends free-text label/ingredient content to the NutriGuard FastAPI
     * backend endpoint: POST /api/v1/scan/ocr-text
     *
     * JSON body per the exact OpenAPI schema (`OcrTextScanRequest`):
     * `{"rawText": "...", "barcode": "..."}` -- NOT `text` (the field is
     * `rawText`, camelCase, matching every other request/response field
     * on this API). [barcode], when non-null/non-blank, is sent as
     * `barcode` so the backend combines this text with an already-known
     * barcode identity into ONE canonical product, exactly like
     * [scanLabelImage]'s optional multipart field -- must always be a
     * REAL barcode the user scanned/typed, never a synthetic
     * `img_.../ocr_...` id a previous standalone response returned.
     *
     * Shares [scanBarcode]/[scanLabelImage]'s exact typed-exception and
     * structured-404 handling ([handleUnsuccessfulResponse]) -- no local
     * fallback analysis and no locally-fabricated Health Score exist on
     * this client anymore; an incomplete result throws
     * [LabelScanRequiredException] like everywhere else, for the caller
     * to treat as a genuine partial result.
     */
    suspend fun scanOcrText(rawText: String, barcode: String? = null): ParsedScanData = withContext(Dispatchers.IO) {
        val cleanedBarcode = barcode?.trim()?.takeIf { it.isNotEmpty() }
        val jsonPayload = buildOcrTextRequestJson(rawText, cleanedBarcode)
        val endpointUrl = "$baseUrl/api/v1/scan/ocr-text"
        Log.d(
            TAG,
            "Executing scanOcrText POST request to: $endpointUrl " +
                "(barcode: ${if (cleanedBarcode != null) "present" else "none"})"
        )

        val request = Request.Builder()
            .url(endpointUrl)
            .post(jsonPayload.toString().toRequestBody("application/json".toMediaType()))
            .header("Accept", "application/json")
            .withScanTrace()
            .build()

        // Deliberately the base httpClient (same one scanLabelImage
        // uses), NOT barcodeHttpClient's tighter callTimeout -- OCR-text
        // analysis can invoke the same server-side Gemini/translation
        // processing a label-image scan does, and no new timeout
        // configuration was requested for this endpoint.
        val response = try {
            scanHttpClient.newCall(request).execute()
        } catch (e: SocketTimeoutException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_TIMEOUT
            Log.w(TAG, "OCR text analysis timed out calling $endpointUrl")
            throw BarcodeTimeoutException(
                "The text analysis is taking too long. Please check your connection and try again.",
                e
            )
        } catch (e: IOException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_OFFLINE
            Log.e(TAG, "Network connection failure to $endpointUrl: ${e.localizedMessage}", e)
            throw BarcodeNetworkException(
                "Unable to reach the NutriGuard server. Check your network connection and try again.",
                e
            )
        }

        response.use { resp ->
            recordResponse(resp)
            val responseBody = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                handleUnsuccessfulResponse(resp.code, responseBody)
            }

            try {
                val jsonObject = JSONObject(responseBody)
                val dto = ScanLabelImageResponseDto.fromJson(jsonObject)
                val parsed = dto.toParsedEntities()
                coroutineContext[ScanTraceContext]?.record?.invoke(LocalScanStage.PARSING, LocalScanOutcome.SUCCEEDED)
                return@withContext parsed
            } catch (e: Exception) {
                coroutineContext[ScanTraceContext]?.let { it.reason = com.example.data.diagnostics.ScanReason.DECODE_ERROR; it.record(LocalScanStage.PARSING, LocalScanOutcome.FAILED) }
                Log.e(TAG, "Failed to parse backend response JSON")
                throw BarcodeParseException("Received an unexpected response from the server.", e)
            }
        }
    }

    /**
     * Sends an image bitmap to the NutriGuard FastAPI backend endpoint:
     * POST /api/v1/scan/label-image
     *
     * [barcode], when non-null/non-blank, is sent as the optional
     * multipart `barcode` field so the backend combines this label
     * photo with an already-known barcode identity into ONE canonical
     * product instead of a synthetic `img_...` row (see
     * `app/api/v1/scan.py::scan_label_image`'s `barcode` form field). It
     * must always be a REAL barcode the user scanned/typed -- never a
     * synthetic `img_.../ocr_...` id a previous standalone response
     * returned (see `MainViewModel`'s docs on `pendingBarcode`).
     *
     * On a structured "not found / label scan required" response throws
     * [LabelScanRequiredException] exactly like [scanBarcode] does -- a
     * label photo can be genuinely useful (e.g. it verified the
     * ingredients but the nutrition panel wasn't legible) without being
     * a complete result, and callers must treat that as a partial
     * result to build on, not a generic failure. On any other failure
     * throws the same [BarcodeScanException] hierarchy [scanBarcode]
     * uses, for identical typed error handling on both endpoints.
     */
    suspend fun scanLabelImage(bitmap: Bitmap, barcode: String? = null): ParsedScanData = withContext(Dispatchers.IO) {
        coroutineContext[ScanTraceContext]?.record?.invoke(LocalScanStage.IMAGE_PREPARATION, LocalScanOutcome.STARTED)
        val stream = ByteArrayOutputStream()
        val compressSuccess = bitmap.compress(Bitmap.CompressFormat.JPEG, 80, stream)
        if (!compressSuccess) {
            coroutineContext[ScanTraceContext]?.let {
                it.reason = com.example.data.diagnostics.ScanReason.DECODE_ERROR
                it.record(LocalScanStage.IMAGE_PREPARATION, LocalScanOutcome.FAILED)
            }
            throw IOException("Failed to compress image bitmap to JPEG format")
        }
        val byteArray = stream.toByteArray()
        coroutineContext[ScanTraceContext]?.recordMetrics?.invoke(
            LocalScanStage.IMAGE_PREPARATION,
            LocalScanOutcome.SUCCEEDED,
            mapOf(
                "imageWidthPx" to bitmap.width.toDouble(),
                "imageHeightPx" to bitmap.height.toDouble(),
                "imageBytes" to byteArray.size.toDouble(),
                "uploadBytes" to byteArray.size.toDouble()
            )
        )
        val cleanedBarcode = barcode?.trim()?.takeIf { it.isNotEmpty() }
        val requestBody = buildLabelImageRequestBody(byteArray, cleanedBarcode)

        val endpointUrl = "$baseUrl/api/v1/scan/label-image"
        Log.d(
            TAG,
            "Executing scanLabelImage POST request to: $endpointUrl " +
                "(payload size: ${byteArray.size} bytes, barcode: ${if (cleanedBarcode != null) "present" else "none"})"
        )

        val request = Request.Builder()
            .url(endpointUrl)
            .post(requestBody)
            .header("Accept", "application/json")
            .withScanTrace()
            .build()

        // Deliberately the base httpClient, unmodified -- image upload
        // must keep its existing connect/read/write timeouts exactly as
        // configured above, not the barcode-lookup call's shorter bound.
        val response = try {
            scanHttpClient.newCall(request).execute()
        } catch (e: SocketTimeoutException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_TIMEOUT
            Log.w(TAG, "Label image upload timed out calling $endpointUrl")
            throw BarcodeTimeoutException(
                "The label analysis is taking too long. Please check your connection and try again.",
                e
            )
        } catch (e: IOException) {
            coroutineContext[ScanTraceContext]?.reason = com.example.data.diagnostics.ScanReason.NETWORK_OFFLINE
            Log.e(TAG, "Network connection failure to $endpointUrl: ${e.localizedMessage}", e)
            throw BarcodeNetworkException(
                "Unable to reach the NutriGuard server. Check your network connection and try again.",
                e
            )
        }

        response.use { resp ->
            recordResponse(resp)
            val responseBody = resp.body?.string() ?: ""
            if (!resp.isSuccessful) {
                handleUnsuccessfulResponse(resp.code, responseBody)
            }

            try {
                val jsonObject = JSONObject(responseBody)
                val dto = ScanLabelImageResponseDto.fromJson(jsonObject)
                val parsed = dto.toParsedEntities()
                coroutineContext[ScanTraceContext]?.record?.invoke(LocalScanStage.PARSING, LocalScanOutcome.SUCCEEDED)
                return@withContext parsed
            } catch (e: Exception) {
                coroutineContext[ScanTraceContext]?.let { it.reason = com.example.data.diagnostics.ScanReason.DECODE_ERROR; it.record(LocalScanStage.PARSING, LocalScanOutcome.FAILED) }
                Log.e(TAG, "Failed to parse backend response JSON")
                throw BarcodeParseException("Received an unexpected response from the server.", e)
            }
        }
    }

    /**
     * Shared non-2xx handling for both [scanBarcode] and [scanLabelImage]
     * -- both endpoints use the identical error envelope, including the
     * structured `PRODUCT_NOT_FOUND` / `labelScanRequired` shape (see
     * [LabelScanRequiredException]'s docs on the additive V12 fields). A
     * 404 is NOT automatically that exception -- only a 404 whose body
     * actually parses as that documented shape is; any other 404 (or
     * any other non-2xx) falls through to [BarcodeServerException] /
     * [BarcodeAuthException], same as before. Always throws -- never
     * returns normally.
     */
    private fun handleUnsuccessfulResponse(statusCode: Int, responseBody: String): Nothing {
        if (statusCode == 404) {
            val backendError = BackendErrorDto.fromJson(responseBody)
            val details = backendError?.details
            if (backendError?.code == "PRODUCT_NOT_FOUND" && details?.labelScanRequired == true) {
                throw LabelScanRequiredException(
                    reason = details.reason
                        ?: "This product could not be fully identified.",
                    suggestedAction = details.suggestedAction,
                    providersChecked = details.providersChecked,
                    discoveredIdentity = details.discoveredIdentity,
                    analysisComplete = details.analysisComplete,
                    healthScoreAvailable = details.healthScoreAvailable,
                    healthScore = details.healthScore,
                    nutritionScanRequired = details.nutritionScanRequired,
                    ingredientsScanRequired = details.ingredientsScanRequired,
                    ingredients = details.ingredients
                )
            }
        }
        if (statusCode == 401 || statusCode == 403) {
            // AuthInterceptor already retried once with a refreshed/
            // re-authenticated token (see its docs); reaching here
            // means that also failed, so the device genuinely
            // couldn't be authenticated -- not just "a server error".
            Log.e(TAG, "NutriGuard Backend authentication failure HTTP $statusCode")
            throw BarcodeAuthException(
                statusCode,
                "Your session could not be verified. Please restart the app and try again."
            )
        }
        Log.e(TAG, "NutriGuard Backend error HTTP $statusCode")
        throw BarcodeServerException(
            statusCode,
            "NutriGuard backend returned an unexpected error (HTTP $statusCode). Please try again."
        )
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

        /**
         * Builds the full multipart body for POST /api/v1/scan/label-image:
         * the required "image" part, plus an OPTIONAL "barcode" text part
         * (see `app/api/v1/scan.py::scan_label_image`'s `barcode` form
         * field) -- added ONLY when [barcode] is non-null (the caller is
         * responsible for trimming/blank-filtering it first; this
         * function trusts whatever it's given, matching the backend's
         * own "omitted, blank, or a literal placeholder -> behaves
         * exactly as if not sent" tolerance one layer up in
         * [scanLabelImage]).
         */
        internal fun buildLabelImageRequestBody(byteArray: ByteArray, barcode: String?): MultipartBody {
            val builder = MultipartBody.Builder()
                .setType(MultipartBody.FORM)
                .addPart(buildLabelImagePart(byteArray))
            if (barcode != null) {
                builder.addFormDataPart("barcode", barcode)
            }
            return builder.build()
        }

        /**
         * Builds the JSON body for POST /api/v1/scan/ocr-text, matching
         * the backend's `OcrTextScanRequest` schema exactly: `rawText`
         * (required), and `barcode` (added ONLY when non-null -- the
         * caller is responsible for trimming/blank-filtering it first,
         * same convention as [buildLabelImageRequestBody]).
         */
        internal fun buildOcrTextRequestJson(rawText: String, barcode: String?): JSONObject {
            return JSONObject().apply {
                put("rawText", rawText)
                if (barcode != null) {
                    put("barcode", barcode)
                }
            }
        }
    }
}
