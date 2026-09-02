package com.example.ui.viewmodel

import android.content.Context
import android.graphics.Bitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.example.data.db.AppDatabase
import com.example.data.model.IngredientEntity
import com.example.data.model.ProductEntity
import com.example.data.model.RiskLevel
import com.example.data.model.ScanHistoryEntity
import com.example.data.model.UserHealthProfile
import com.example.data.remote.BarcodeAuthException
import com.example.data.remote.BarcodeNetworkException
import com.example.data.remote.BarcodeScanException
import com.example.data.remote.BarcodeTimeoutException
import com.example.data.remote.DiscoveredIdentity
import com.example.data.remote.LabelScanRequiredException
import com.example.data.remote.dto.toEntities
import com.example.data.repository.FullProductAnalysis
import com.example.data.repository.ProductAnalysisSource
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface AnalysisUiState {
    object Idle : AnalysisUiState
    object Loading : AnalysisUiState
    data class Success(val analysis: FullProductAnalysis) : AnalysisUiState
    data class Error(val message: String) : AnalysisUiState
}

/**
 * Drives the Scan screen's reaction to EITHER a barcode lookup
 * (`scanBarcode`) OR a label-image submission (`analyzeLabelImage`) --
 * both endpoints share this one state machine (and this one card area
 * on `ScanHomeScreen`) because they now share the identical result
 * shape: a genuine success, a structured partial ("more evidence
 * needed") result, or a retryable failure. The screen stays put for
 * the whole [Searching] window and only navigates to Product Details
 * once a call resolves with a genuine success (see
 * [navigateToResultEvent]) -- never eagerly, and never for
 * [LabelScanRequired] or [Failed].
 */
sealed interface BarcodeLookupUiState {
    object Idle : BarcodeLookupUiState
    object Searching : BarcodeLookupUiState

    /**
     * Backend returned `PRODUCT_NOT_FOUND` with `details.labelScanRequired = true`
     * -- the product/photo is not yet enough for a confident analysis,
     * but never a fabricated one either. [discoveredIdentity] is only
     * non-null when the backend actually found something worth
     * showing.
     *
     * [analysisComplete]/[healthScoreAvailable]/[healthScore]/
     * [nutritionScanRequired]/[ingredientsScanRequired]/[ingredients]
     * mirror the backend's V12 additive partial-analysis payload and
     * are only populated for a genuine partial result -- all `null`/
     * empty for a plain "barcode not found anywhere" 404. [healthScore]
     * must NEVER be treated as `0` when `healthScoreAvailable` is false
     * or absent -- it means "not computed", not "a score of zero".
     * [ingredients] is the ALREADY-VERIFIED evidence for this row (from
     * a prior scan, or from THIS submission) -- safe to show as-is, it
     * is never fabricated.
     */
    data class LabelScanRequired(
        val reason: String,
        val suggestedAction: String?,
        val discoveredIdentity: DiscoveredIdentity?,
        val analysisComplete: Boolean?,
        val healthScoreAvailable: Boolean?,
        val healthScore: Int?,
        val nutritionScanRequired: Boolean?,
        val ingredientsScanRequired: Boolean?,
        val ingredients: List<IngredientEntity>
    ) : BarcodeLookupUiState {
        companion object {
            fun from(e: LabelScanRequiredException, idPrefixForSyntheticIds: String): LabelScanRequired =
                LabelScanRequired(
                    reason = e.reason,
                    suggestedAction = e.suggestedAction,
                    discoveredIdentity = e.discoveredIdentity,
                    analysisComplete = e.analysisComplete,
                    healthScoreAvailable = e.healthScoreAvailable,
                    healthScore = e.healthScore,
                    nutritionScanRequired = e.nutritionScanRequired,
                    ingredientsScanRequired = e.ingredientsScanRequired,
                    ingredients = e.ingredients.toEntities(idPrefixForSyntheticIds)
                )
        }
    }

    /** Network failure, timeout, or an unexpected server/parse error -- always retryable. */
    data class Failed(val message: String, val barcode: String) : BarcodeLookupUiState
}

class MainViewModel(
    private val repository: ProductAnalysisSource
) : ViewModel() {

    private val _analysisState = MutableStateFlow<AnalysisUiState>(AnalysisUiState.Idle)
    val analysisState: StateFlow<AnalysisUiState> = _analysisState.asStateFlow()

    private val _barcodeLookupState = MutableStateFlow<BarcodeLookupUiState>(BarcodeLookupUiState.Idle)
    val barcodeLookupState: StateFlow<BarcodeLookupUiState> = _barcodeLookupState.asStateFlow()

    // The real barcode a `LabelScanRequired` result is for -- set the
    // moment `scanBarcode` receives it (see that function), so a
    // follow-up label-image submission (`analyzeLabelImage`) can attach
    // it via the backend's optional `barcode` multipart field and
    // combine both sources into ONE product. Deliberately a SEPARATE
    // StateFlow from [barcodeLookupState]: dismissing/replacing the
    // CARD (`dismissBarcodeLookupState`) must not lose this -- it is
    // only ever cleared by a genuinely successful analysis or an
    // explicit whole-flow cancellation (`cancelPendingScanFlow`), never
    // implicitly. NEVER a synthetic `img_.../ocr_...` id -- only
    // `scanBarcode`'s own (real, user-submitted) barcode argument sets
    // this; a standalone label-image response's synthetic id is never
    // adopted here.
    private val _pendingBarcode = MutableStateFlow<String?>(null)
    val pendingBarcode: StateFlow<String?> = _pendingBarcode.asStateFlow()

    // One-shot "navigate to Product Details now" signal for a successful
    // barcode lookup -- a StateFlow would re-fire navigation on every
    // recomposition/state re-collection (e.g. after a config change);
    // this fires exactly once per successful scan.
    private val _navigateToResultEvent = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    val navigateToResultEvent: SharedFlow<Unit> = _navigateToResultEvent.asSharedFlow()

    private val _ingredientSearchQuery = MutableStateFlow("")
    val ingredientSearchQuery: StateFlow<String> = _ingredientSearchQuery.asStateFlow()

    private val _selectedRiskFilter = MutableStateFlow<RiskLevel?>(null)
    val selectedRiskFilter: StateFlow<RiskLevel?> = _selectedRiskFilter.asStateFlow()

    val allIngredients: StateFlow<List<IngredientEntity>> = repository.allIngredients
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val userProfile: StateFlow<UserHealthProfile?> = repository.userProfile
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)

    val scanHistory: StateFlow<List<ScanHistoryEntity>> = repository.scanHistory
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val allProducts: StateFlow<List<ProductEntity>> = repository.allProducts
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    /**
     * Looks up [barcode] against the backend. Stays on the Scan screen
     * the whole time ([BarcodeLookupUiState.Searching]); only on a
     * genuine success does [analysisState] become `Success` and
     * [navigateToResultEvent] fire. A structured "label scan required"
     * response, a network failure, a timeout, or any other error all
     * resolve to a distinct, non-navigating [BarcodeLookupUiState] —
     * see that type's docs. Re-entrant calls while a lookup is already
     * in flight are ignored (prevents repeated submissions from a
     * double-tap or a second scan before the first one resolves).
     */
    fun scanBarcode(barcode: String) {
        if (_barcodeLookupState.value is BarcodeLookupUiState.Searching) return
        // Set synchronously, before launch: a coroutine launched on
        // viewModelScope doesn't necessarily start running before this
        // function returns (e.g. StandardTestDispatcher, or a second
        // real UI tap landing before the first coroutine gets a
        // dispatch slot), so the early-return check above must never
        // observe a stale Idle from a lookup that's already "in flight"
        // but hasn't run its first suspend point yet.
        _barcodeLookupState.value = BarcodeLookupUiState.Searching
        viewModelScope.launch {
            try {
                val result = repository.analyzeBarcode(barcode)
                _analysisState.value = AnalysisUiState.Success(result)
                _barcodeLookupState.value = BarcodeLookupUiState.Idle
                // A genuine, complete result -- clear any stale pending
                // barcode from an earlier, now-superseded incomplete flow.
                _pendingBarcode.value = null
                _navigateToResultEvent.tryEmit(Unit)
            } catch (e: LabelScanRequiredException) {
                // Requirement: never clear the pending barcode BEFORE the
                // request -- this IS the request that establishes it, so
                // set it here, unconditionally (overwriting whatever was
                // pending before, if anything, since this is a fresh
                // user-initiated lookup for THIS barcode).
                _pendingBarcode.value = barcode
                _barcodeLookupState.value = BarcodeLookupUiState.LabelScanRequired.from(e, barcode)
            } catch (e: BarcodeTimeoutException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "The request timed out. Please try again.",
                    barcode
                )
            } catch (e: BarcodeNetworkException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Unable to reach the server. Please check your connection.",
                    barcode
                )
            } catch (e: BarcodeAuthException) {
                // Distinct from BarcodeServerException: AuthInterceptor already
                // tried a token refresh/device re-auth once and it still failed,
                // so a plain retry of the same request would fail identically.
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Your session could not be verified. Please restart the app and try again.",
                    barcode
                )
            } catch (e: BarcodeScanException) {
                // BarcodeServerException / BarcodeParseException: unexpected
                // server-side or response-shape failure -- generic, retryable.
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Something went wrong looking up this product. Please try again.",
                    barcode
                )
            } catch (e: Exception) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.localizedMessage ?: "Something went wrong looking up this product. Please try again.",
                    barcode
                )
            }
        }
    }

    /**
     * Dismisses a [BarcodeLookupUiState.LabelScanRequired] or
     * [BarcodeLookupUiState.Failed] card back to [BarcodeLookupUiState.Idle]
     * WITHOUT navigating anywhere and WITHOUT clearing [pendingBarcode]
     * -- e.g. tapping "Scan label for more information" hides the card
     * (to make room for the camera) but the flow is still very much
     * active. Use [cancelPendingScanFlow] to actually abandon the flow.
     */
    fun dismissBarcodeLookupState() {
        _barcodeLookupState.value = BarcodeLookupUiState.Idle
    }

    /**
     * Explicitly abandons the current pending-barcode flow (e.g. the
     * user taps "Dismiss" on a [BarcodeLookupUiState.LabelScanRequired]
     * card rather than taking another photo) -- clears BOTH the card
     * state AND [pendingBarcode], so a later label-image submission
     * starts a fresh, unlinked (standalone) analysis instead of
     * silently still attaching an abandoned barcode.
     */
    fun cancelPendingScanFlow() {
        _barcodeLookupState.value = BarcodeLookupUiState.Idle
        _pendingBarcode.value = null
    }

    /**
     * Submits typed/pasted label text (the "Manual Barcode or Text
     * Lookup" panel on the Scan screen) for analysis via
     * `POST /api/v1/scan/ocr-text` (review round 2, finding 2). Shares
     * [barcodeLookupState] with [scanBarcode]/[analyzeLabelImage] (see
     * that state's docs) -- stays on the Scan screen showing
     * [BarcodeLookupUiState.Searching], and only navigates on a
     * genuine, complete success; a structured partial result shows the
     * verified evidence and what's still missing, exactly like a label
     * photo, never a locally-fabricated Health Score.
     *
     * [pendingBarcode] is attached automatically exactly like
     * [analyzeLabelImage] -- a barcode-linked text submission keeps it
     * until a genuine full success or an explicit cancel; a standalone
     * submission (no pending barcode) never adopts the synthetic
     * `ocr_.../img_...` id a plain response's own identity carries.
     */
    fun analyzeOcrText(text: String) {
        if (_barcodeLookupState.value is BarcodeLookupUiState.Searching) return
        _barcodeLookupState.value = BarcodeLookupUiState.Searching
        val barcodeForThisSubmission = _pendingBarcode.value
        viewModelScope.launch {
            try {
                val result = repository.analyzeOcrText(text, barcodeForThisSubmission)
                _analysisState.value = AnalysisUiState.Success(result)
                _barcodeLookupState.value = BarcodeLookupUiState.Idle
                _pendingBarcode.value = null
                _navigateToResultEvent.tryEmit(Unit)
            } catch (e: LabelScanRequiredException) {
                // Still incomplete -- a useful partial result, not a
                // generic error (never navigate away). pendingBarcode is
                // left exactly as it was, same rationale as
                // analyzeLabelImage: unchanged if barcode-linked (so a
                // follow-up photo/text submission keeps combining into
                // the same product), still null for a standalone
                // submission.
                val idPrefix = barcodeForThisSubmission ?: e.discoveredIdentity?.barcode ?: "ocr_text"
                _barcodeLookupState.value = BarcodeLookupUiState.LabelScanRequired.from(e, idPrefix)
            } catch (e: BarcodeTimeoutException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "The request timed out. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeNetworkException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Unable to reach the server. Please check your connection.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeAuthException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Your session could not be verified. Please restart the app and try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeScanException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Something went wrong analyzing this text. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: Exception) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.localizedMessage ?: "Something went wrong analyzing this text. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            }
        }
    }

    /**
     * Submits a label photo (Ingredient Label camera capture OR a
     * gallery pick -- both routes call this same function, see
     * `ScanHomeScreen`) for analysis. Shares [barcodeLookupState] with
     * [scanBarcode] (see that state's docs): stays on the Scan screen
     * showing [BarcodeLookupUiState.Searching], and only navigates on a
     * genuine, complete success.
     *
     * If [pendingBarcode] is currently set (a prior `scanBarcode` call
     * returned `labelScanRequired`, or an earlier photo for the SAME
     * barcode was itself still incomplete), it is automatically
     * attached to this submission so the backend combines this photo
     * with that barcode identity into ONE product -- this is what lets
     * two sequential photos (e.g. nutrition panel, then ingredients
     * list) merge into the same result. [pendingBarcode] is cleared
     * only on a genuine full success; a request that comes back STILL
     * incomplete (another [LabelScanRequiredException]) leaves it
     * untouched, so a follow-up photo keeps combining into the same
     * product. Standalone submissions (no [pendingBarcode] set) are
     * unaffected either way -- exactly the pre-existing behavior.
     */
    fun analyzeLabelImage(bitmap: Bitmap) {
        if (_barcodeLookupState.value is BarcodeLookupUiState.Searching) return
        _barcodeLookupState.value = BarcodeLookupUiState.Searching
        val barcodeForThisSubmission = _pendingBarcode.value
        viewModelScope.launch {
            try {
                val result = repository.analyzeImageLabel(bitmap, barcodeForThisSubmission)
                _analysisState.value = AnalysisUiState.Success(result)
                _barcodeLookupState.value = BarcodeLookupUiState.Idle
                _pendingBarcode.value = null
                _navigateToResultEvent.tryEmit(Unit)
            } catch (e: LabelScanRequiredException) {
                // Still incomplete -- a useful partial result, not a
                // generic error (never navigate away). pendingBarcode is
                // deliberately left exactly as it was: unchanged if this
                // submission was barcode-linked (so the NEXT photo keeps
                // combining into the same product), still null if this
                // was a standalone submission (never adopt the
                // synthetic id a standalone response's own identity
                // carries -- see LabelScanRequiredException's docs).
                val idPrefix = barcodeForThisSubmission ?: e.discoveredIdentity?.barcode ?: "label_scan"
                _barcodeLookupState.value = BarcodeLookupUiState.LabelScanRequired.from(e, idPrefix)
            } catch (e: BarcodeTimeoutException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "The request timed out. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeNetworkException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Unable to reach the server. Please check your connection.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeAuthException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Your session could not be verified. Please restart the app and try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: BarcodeScanException) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.message ?: "Something went wrong analyzing this label. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            } catch (e: Exception) {
                _barcodeLookupState.value = BarcodeLookupUiState.Failed(
                    e.localizedMessage ?: "Something went wrong analyzing this label. Please try again.",
                    barcodeForThisSubmission ?: ""
                )
            }
        }
    }

    fun updateProfile(profile: UserHealthProfile) {
        viewModelScope.launch {
            repository.saveUserProfile(profile)
            // Re-trigger current analysis if active. Deliberately NOT
            // scanBarcode(): this is a silent re-score of the product
            // already on screen (always a local-cache hit, since it was
            // just successfully analyzed), not a new user-initiated
            // lookup -- it must not touch BarcodeLookupUiState or fire
            // navigateToResultEvent (the user may not even be on the
            // Scan screen right now).
            val current = _analysisState.value
            if (current is AnalysisUiState.Success) {
                try {
                    val result = repository.analyzeBarcode(current.analysis.product.barcode)
                    _analysisState.value = AnalysisUiState.Success(result)
                } catch (e: Exception) {
                    // Best-effort refresh; keep showing the previous analysis on failure.
                }
            }
        }
    }

    fun setIngredientSearchQuery(query: String) {
        _ingredientSearchQuery.value = query
    }

    fun setRiskFilter(filter: RiskLevel?) {
        _selectedRiskFilter.value = filter
    }

    fun resetState() {
        _analysisState.value = AnalysisUiState.Idle
    }

    class Factory(private val repository: ProductAnalysisSource) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return MainViewModel(repository) as T
        }
    }
}
