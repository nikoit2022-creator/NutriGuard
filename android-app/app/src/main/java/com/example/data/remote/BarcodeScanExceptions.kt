package com.example.data.remote

import com.example.data.remote.dto.IngredientDto
import com.example.data.remote.dto.cleanOrNull
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException

/**
 * Typed failures for `POST /api/v1/scan/barcode` (see [NutriGuardApiService.scanBarcode]),
 * mirroring the [com.example.data.auth.DeviceAuthException] hierarchy so
 * `MainViewModel` can dispatch on failure *kind* rather than guessing
 * from a message string or treating every non-2xx as the same thing.
 *
 * IMPORTANT: an HTTP 404 is NOT automatically [LabelScanRequiredException] —
 * only a 404 whose body actually parses as the documented
 * `error.code == "PRODUCT_NOT_FOUND"` + `error.details.labelScanRequired == true`
 * envelope is. A 404 with any other/missing shape becomes
 * [BarcodeServerException] instead, same as any other unexpected
 * server error.
 */
sealed class BarcodeScanException(message: String, cause: Throwable? = null) : IOException(message, cause)

/** No response was received at all (DNS/connect failure, socket reset, etc.). */
class BarcodeNetworkException(message: String, cause: Throwable? = null) : BarcodeScanException(message, cause)

/** The request exceeded its connect/read/call timeout. */
class BarcodeTimeoutException(message: String, cause: Throwable? = null) : BarcodeScanException(message, cause)

/** A non-2xx response that isn't the structured "label scan required" case. */
class BarcodeServerException(val statusCode: Int, message: String) : BarcodeScanException(message)

/**
 * A 401/403 that survived [com.example.data.auth.AuthInterceptor]'s
 * transparent refresh/device-re-auth retry -- i.e. the device could not
 * be (re-)authenticated at all, not merely "an access token happened to
 * expire". Kept distinct from [BarcodeServerException] because blindly
 * retrying the same request again would just fail the same way; the
 * caller should tell the user their session couldn't be verified.
 */
class BarcodeAuthException(val statusCode: Int, message: String) : BarcodeScanException(message)

/** A 2xx (or otherwise unexpected-shape) body that could not be parsed. */
class BarcodeParseException(message: String, cause: Throwable? = null) : BarcodeScanException(message, cause)

/**
 * The barcode was recognized as `PRODUCT_NOT_FOUND` with
 * `details.labelScanRequired = true`: the product is unknown, or was
 * found but is too incomplete for a confident analysis. Never carries
 * fabricated product data — [discoveredIdentity] is only populated when
 * the backend actually found *something* worth showing.
 *
 * [suggestedAction] is parsed for completeness but deliberately UNUSED
 * for the primary call-to-action text in the UI (the backend's raw
 * `suggestedAction` string is a technical/generic hint, not a
 * user-facing label) — callers should always render a fixed
 * "Scan label for more information" action instead.
 *
 * The remaining fields mirror the backend's V12 additive partial-
 * analysis payload (see `_label_scan_required_details` in
 * `nutriguard-backend/app/services/food_analysis.py`) and are only
 * populated for a genuine partial result (an identity WAS
 * discovered/persisted) — for a plain "nothing found anywhere" 404
 * (`_not_found_details`, no such row exists yet), all five are `null`/
 * empty, since the backend never sends them for that shape. A `null`
 * [healthScore] must NEVER be rendered as `0` — it means "not computed
 * at all", not "a score of zero".
 */
class LabelScanRequiredException(
    val reason: String,
    val suggestedAction: String?,
    val providersChecked: List<String>,
    val discoveredIdentity: DiscoveredIdentity?,
    val analysisComplete: Boolean? = null,
    val healthScoreAvailable: Boolean? = null,
    val healthScore: Int? = null,
    val nutritionScanRequired: Boolean? = null,
    val ingredientsScanRequired: Boolean? = null,
    val ingredients: List<IngredientDto> = emptyList()
) : BarcodeScanException(reason)

/**
 * Domain form of `error.details.discoveredIdentity`. Every field is
 * independently nullable/placeholder-filtered ([cleanOrNull]) — a
 * partially-known identity (e.g. a name but no brand) is expected and
 * must render safely rather than show a "null"/blank value.
 */
data class DiscoveredIdentity(
    val barcode: String?,
    val productName: String?,
    val brand: String?,
    val imageUrl: String?
) {
    /** True when there's nothing safe/non-placeholder to actually show the user. */
    val isEmpty: Boolean
        get() = productName == null && brand == null && imageUrl == null
}

/**
 * Mirrors the backend's standard error envelope:
 * `{"error": {"code", "message", "details": {...}, "timestamp"}}`.
 * Parsing tolerates any missing/malformed field — see [fromJson], which
 * returns `null` rather than throwing on anything unexpected, so a
 * backend response that doesn't match this shape never crashes the app.
 */
data class BackendErrorDto(
    val code: String?,
    val message: String?,
    val details: BackendErrorDetailsDto?
) {
    companion object {
        /** Never throws: returns `null` for any missing/malformed structure. */
        fun fromJson(body: String): BackendErrorDto? {
            return try {
                val root = JSONObject(body)
                val errorObj = root.optJSONObject("error") ?: return null
                BackendErrorDto(
                    code = errorObj.optString("code").cleanOrNull(),
                    message = errorObj.optString("message").cleanOrNull(),
                    details = errorObj.optJSONObject("details")?.let(BackendErrorDetailsDto::fromJson)
                )
            } catch (e: Exception) {
                null
            }
        }
    }
}

/**
 * Mirrors the backend's V12 additive partial-analysis fields (see
 * `_label_scan_required_details`): [analysisComplete]/
 * [healthScoreAvailable]/[healthScore]/[nutritionScanRequired]/
 * [ingredientsScanRequired]/[ingredients] are all `null`/empty when the
 * backend didn't send them (the plain "nothing found anywhere" 404
 * shape, `_not_found_details`, predates these fields and never carries
 * them) — a client must treat "absent" the same as "unknown", never
 * infer `false`/`0`/complete from a missing field.
 */
data class BackendErrorDetailsDto(
    val labelScanRequired: Boolean,
    val reason: String?,
    val suggestedAction: String?,
    val providersChecked: List<String>,
    val discoveredIdentity: DiscoveredIdentity?,
    val analysisComplete: Boolean?,
    val healthScoreAvailable: Boolean?,
    val healthScore: Int?,
    val nutritionScanRequired: Boolean?,
    val ingredientsScanRequired: Boolean?,
    val ingredients: List<IngredientDto>
) {
    companion object {
        fun fromJson(details: JSONObject): BackendErrorDetailsDto {
            val providers = mutableListOf<String>()
            val providersArray: JSONArray? = details.optJSONArray("providersChecked")
            if (providersArray != null) {
                for (i in 0 until providersArray.length()) {
                    val entry = providersArray.optJSONObject(i)
                    val provider = entry?.optString("provider")?.cleanOrNull()
                    if (provider != null) providers.add(provider)
                }
            }

            val identityObj = details.optJSONObject("discoveredIdentity")
            val identity = identityObj?.let {
                val parsed = DiscoveredIdentity(
                    barcode = it.optString("barcode").cleanOrNull(),
                    productName = it.optString("productName").cleanOrNull(),
                    brand = it.optString("brand").cleanOrNull(),
                    imageUrl = it.optString("imageUrl").cleanOrNull()
                )
                parsed.takeUnless { d -> d.isEmpty }
            }

            val ingredientsList = mutableListOf<IngredientDto>()
            val ingredientsArray = details.optJSONArray("ingredients")
            if (ingredientsArray != null) {
                for (i in 0 until ingredientsArray.length()) {
                    val ingObj = ingredientsArray.optJSONObject(i)
                    if (ingObj != null) {
                        ingredientsList.add(IngredientDto.fromJson(ingObj))
                    }
                }
            }

            return BackendErrorDetailsDto(
                labelScanRequired = details.optBoolean("labelScanRequired", false),
                reason = details.optString("reason").cleanOrNull(),
                suggestedAction = details.optString("suggestedAction").cleanOrNull(),
                providersChecked = providers,
                discoveredIdentity = identity,
                analysisComplete = details.optNullableBoolean("analysisComplete"),
                healthScoreAvailable = details.optNullableBoolean("healthScoreAvailable"),
                healthScore = details.optNullableInt("healthScore"),
                nutritionScanRequired = details.optNullableBoolean("nutritionScanRequired"),
                ingredientsScanRequired = details.optNullableBoolean("ingredientsScanRequired"),
                ingredients = ingredientsList
            )
        }

        /** `null` when the key is absent OR its value is JSON `null` -- [JSONObject.optBoolean] can't distinguish "absent" from "false" otherwise. */
        private fun JSONObject.optNullableBoolean(key: String): Boolean? =
            if (has(key) && !isNull(key)) optBoolean(key) else null

        /** `null` when the key is absent OR its value is JSON `null` -- [JSONObject.optInt] can't distinguish "absent"/"null" from `0` otherwise. */
        private fun JSONObject.optNullableInt(key: String): Int? =
            if (has(key) && !isNull(key)) optInt(key) else null
    }
}
