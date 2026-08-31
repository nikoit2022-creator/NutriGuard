package com.example.data.remote.dto

/**
 * Shared placeholder-value filtering for backend-supplied display text.
 *
 * The backend's own bilingual/discovery safety nets already scrub these
 * before they reach the wire, but the Android client must never trust a
 * single layer of defense for text it shows directly to the user (a
 * malformed/older backend response, or a bug on either side, must not
 * surface a literal "null" or an empty label). Used for barcode
 * discovery's `discoveredIdentity` fields and error `reason`/
 * `suggestedAction` text — see [com.example.data.remote.BackendErrorDto].
 */
private val PLACEHOLDER_VALUES = setOf(
    "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown"
)

/**
 * Trims [this] and returns it, unless it's blank or a known literal
 * placeholder ("null", "None", "undefined", "N/A", ...), in which case
 * `null` is returned instead — never the placeholder text itself.
 */
fun String?.cleanOrNull(): String? {
    val trimmed = this?.trim() ?: return null
    if (trimmed.isEmpty()) return null
    if (trimmed.lowercase() in PLACEHOLDER_VALUES) return null
    return trimmed
}
