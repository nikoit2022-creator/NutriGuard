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
 * Drives the Scan screen's reaction to a barcode lookup: it stays on
 * this screen (`ScanHomeScreen`) for the whole [Searching] window and
 * only navigates to Product Details once [scanBarcode] resolves with a
 * genuine success (see [navigateToResultEvent]) -- never eagerly, and
 * never for [LabelScanRequired] or [Failed].
 */
sealed interface BarcodeLookupUiState {
    object Idle : BarcodeLookupUiState
    object Searching : BarcodeLookupUiState

    /**
     * Backend returned `PRODUCT_NOT_FOUND` with `details.labelScanRequired = true`
     * (barcode unknown, or found but too incomplete for a confident
     * analysis) -- never a fabricated product. [discoveredIdentity] is
     * only non-null when the backend actually found something worth
     * showing.
     */
    data class LabelScanRequired(
        val reason: String,
        val suggestedAction: String?,
        val discoveredIdentity: DiscoveredIdentity?
    ) : BarcodeLookupUiState

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
                _navigateToResultEvent.tryEmit(Unit)
            } catch (e: LabelScanRequiredException) {
                _barcodeLookupState.value = BarcodeLookupUiState.LabelScanRequired(
                    reason = e.reason,
                    suggestedAction = e.suggestedAction,
                    discoveredIdentity = e.discoveredIdentity
                )
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

    /** Dismisses a [BarcodeLookupUiState.LabelScanRequired] or [BarcodeLookupUiState.Failed] card without navigating anywhere. */
    fun dismissBarcodeLookupState() {
        _barcodeLookupState.value = BarcodeLookupUiState.Idle
    }

    fun analyzeOcrText(text: String) {
        viewModelScope.launch {
            _analysisState.value = AnalysisUiState.Loading
            try {
                val result = repository.analyzeOcrText(text)
                _analysisState.value = AnalysisUiState.Success(result)
            } catch (e: Exception) {
                _analysisState.value = AnalysisUiState.Error(e.localizedMessage ?: "Failed to process ingredient text")
            }
        }
    }

    fun analyzeLabelImage(bitmap: Bitmap) {
        viewModelScope.launch {
            _analysisState.value = AnalysisUiState.Loading
            try {
                val result = repository.analyzeImageLabel(bitmap)
                _analysisState.value = AnalysisUiState.Success(result)
            } catch (e: Exception) {
                _analysisState.value = AnalysisUiState.Error(e.localizedMessage ?: "Failed to analyze image label")
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
