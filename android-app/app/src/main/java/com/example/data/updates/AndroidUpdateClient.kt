package com.example.data.updates

import com.example.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

data class AndroidRelease(
    val versionCode: Int,
    val versionName: String,
    val sha256: String,
    val sizeBytes: Long,
    val notes: String,
)

class AndroidUpdateClient(private val cacheDir: File) {
    private val baseUrl = BuildConfig.BACKEND_BASE_URL.trimEnd('/')
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .followRedirects(false)
        .followSslRedirects(false)
        .build()

    suspend fun latest(): AndroidRelease? = withContext(Dispatchers.IO) {
        val request = Request.Builder().url("$baseUrl/api/v1/app-updates/android/latest").build()
        client.newCall(request).execute().use { response ->
            if (response.code == 204) return@withContext null
            if (!response.isSuccessful) throw IOException("Update check failed: HTTP ${response.code}")
            val json = JSONObject(response.body?.string() ?: throw IOException("Empty update response"))
            val release = AndroidRelease(
                versionCode = json.getInt("versionCode"),
                versionName = json.getString("versionName"),
                sha256 = json.getString("sha256"),
                sizeBytes = json.getLong("sizeBytes"),
                notes = json.optString("notes"),
            )
            if (release.versionCode <= 0 || !release.sha256.matches(Regex("[0-9a-f]{64}")) ||
                release.sizeBytes !in 1..MAX_APK_BYTES
            ) throw IOException("Invalid update metadata")
            release
        }
    }

    suspend fun download(release: AndroidRelease): File = withContext(Dispatchers.IO) {
        val directory = File(cacheDir, "app-updates").also { it.mkdirs() }
        val destination = File(directory, "${release.versionCode}.apk")
        if (destination.isFile && verify(destination, release)) return@withContext destination
        val temporary = File(directory, "${release.versionCode}.download")
        try {
            val request = Request.Builder()
                .url("$baseUrl/api/v1/app-updates/android/${release.versionCode}.apk")
                .build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) throw IOException("Update download failed: HTTP ${response.code}")
                val body = response.body ?: throw IOException("Empty update file")
                var count = 0L
                body.byteStream().use { input ->
                    temporary.outputStream().use { output ->
                        val buffer = ByteArray(64 * 1024)
                        while (true) {
                            val read = input.read(buffer)
                            if (read < 0) break
                            count += read
                            if (count > release.sizeBytes) throw IOException("Update exceeds advertised size")
                            output.write(buffer, 0, read)
                        }
                    }
                }
                if (count != release.sizeBytes || !verify(temporary, release)) {
                    throw IOException("Update integrity check failed")
                }
            }
            if (destination.exists()) destination.delete()
            if (!temporary.renameTo(destination)) throw IOException("Could not save update")
            destination
        } finally {
            temporary.delete()
        }
    }

    private fun verify(file: File, release: AndroidRelease): Boolean {
        if (file.length() != release.sizeBytes) return false
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        val actual = digest.digest().joinToString("") { "%02x".format(it) }
        return actual == release.sha256
    }

    companion object {
        private const val MAX_APK_BYTES = 150L * 1024 * 1024
    }
}
