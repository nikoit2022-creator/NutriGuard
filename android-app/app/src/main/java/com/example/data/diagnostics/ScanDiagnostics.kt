package com.example.data.diagnostics

import android.content.Context
import com.example.BuildConfig
import com.example.data.auth.AuthTokenStore
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/** Application-owned, bounded best-effort delivery. Pending disk events survive process death.
 * No auth interceptor: a queued owner's event must never be retried under a new login.
 * Delivery runs while the process lives, and resumes on the next app start (not an OS job).
 */
class ScanDiagnostics(
    context: Context,
    private val tokens: AuthTokenStore,
    private val outbox: ScanDiagnosticOutbox = ScanDiagnosticOutbox(File(context.noBackupFilesDir, "scan-diagnostic-outbox.json")),
    private val client: OkHttpClient = OkHttpClient.Builder().callTimeout(20, TimeUnit.SECONDS)
        .retryOnConnectionFailure(false).followRedirects(false).build(),
    startDelivery: Boolean = true
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val queue = Channel<PendingScanEvent>(256)
    private val dropped = AtomicLong()
    val version = "${BuildConfig.VERSION_NAME}-${BuildConfig.VERSION_CODE}-${BuildConfig.SOURCE_REVISION}"
        .replace(Regex("[^A-Za-z0-9_.+-]"), "_").take(32)

    fun ownerKey(): String? = tokens.getUserId()?.let { id ->
        MessageDigest.getInstance("SHA-256").digest(id.toByteArray()).joinToString("") { "%02x".format(it) }
    }

    fun record(event: PendingScanEvent) {
        if (!BuildConfig.DEBUG) return
        if (!queue.trySend(event.copy(appVersion = version)).isSuccess) dropped.incrementAndGet()
    }

    init {
        if (BuildConfig.DEBUG && startDelivery) {
            scope.launch {
                for (event in queue) if (!outbox.append(event)) dropped.incrementAndGet()
            }
            scope.launch {
                var waitMs = 30_000L
                while (isActive) {
                    delay(waitMs)
                    val lost = dropped.getAndSet(0)
                    if (!outbox.maintain(lost)) dropped.addAndGet(lost)
                    waitMs = if (deliver()) 30_000L else (waitMs * 2).coerceAtMost(30 * 60_000L)
                }
            }
        }
    }

    internal fun deliver(): Boolean {
        // SharedPreferencesAuthTokenStore mutates tokens under this same monitor.
        // Snapshot owner and token together, never combine two different sessions.
        val (owner, token) = synchronized(tokens) {
            (ownerKey() ?: return false) to (tokens.getAccessToken() ?: return false)
        }
        val ownerPending = outbox.pending().filter { it.ownerKey == owner }
        if (ownerPending.isEmpty()) return true
        val queuedAt = System.currentTimeMillis()
        val deliveryBatch = ScanEventWire.batch(ownerPending.map { event ->
            event.copy(metrics = event.metrics + mapOf(
                "outboxDepth" to ownerPending.size.toDouble(),
                "queuedMs" to (queuedAt - event.occurredAt).coerceAtLeast(0).coerceAtMost(86_400_000).toDouble()
            ))
        })
        if (deliveryBatch.isEmpty()) return false
        return try {
            val request = Request.Builder()
                .url(BuildConfig.BACKEND_BASE_URL.trimEnd('/') + "/api/v1/scan-diagnostics/client-events")
                .header("Authorization", "Bearer $token")
                .post(ScanEventWire.body(deliveryBatch).toRequestBody("application/json".toMediaType())).build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return false
                val body = response.body ?: return false
                val bytes = body.byteStream().use { stream ->
                    val buffer = ByteArray(16 * 1024 + 1)
                    var count = 0
                    while (count < buffer.size) {
                        val read = stream.read(buffer, count, buffer.size - count)
                        if (read < 0) break
                        count += read
                    }
                    if (count > 16 * 1024) return false
                    buffer.copyOf(count)
                }
                val ack = ScanEventWire.acknowledged(bytes.toString(Charsets.UTF_8), deliveryBatch.map { it.eventId }.toSet())
                outbox.acknowledge(ack) && ack.size == deliveryBatch.size
            }
        } catch (_: Exception) { false }
    }
}
