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
import java.util.concurrent.TimeUnit

class NutriGuardApiService(
    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS)
        .build()
) {
    private val baseUrl: String = BuildConfig.BACKEND_BASE_URL.trimEnd('/')

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
            .addFormDataPart(
                name = "file",
                filename = "food_label.jpg",
                body = byteArray.toRequestBody("image/jpeg".toMediaType())
            )
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
    }
}
