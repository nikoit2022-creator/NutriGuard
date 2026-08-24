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
import com.example.data.repository.FoodAnalysisRepository
import com.example.data.repository.FullProductAnalysis
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

sealed interface AnalysisUiState {
    object Idle : AnalysisUiState
    object Loading : AnalysisUiState
    data class Success(val analysis: FullProductAnalysis) : AnalysisUiState
    data class Error(val message: String) : AnalysisUiState
}

class MainViewModel(
    private val repository: FoodAnalysisRepository
) : ViewModel() {

    private val _analysisState = MutableStateFlow<AnalysisUiState>(AnalysisUiState.Idle)
    val analysisState: StateFlow<AnalysisUiState> = _analysisState.asStateFlow()

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

    fun scanBarcode(barcode: String) {
        viewModelScope.launch {
            _analysisState.value = AnalysisUiState.Loading
            try {
                val result = repository.analyzeBarcode(barcode)
                _analysisState.value = AnalysisUiState.Success(result)
            } catch (e: Exception) {
                _analysisState.value = AnalysisUiState.Error(e.localizedMessage ?: "Failed to analyze barcode")
            }
        }
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
            // Re-trigger current analysis if active
            val current = _analysisState.value
            if (current is AnalysisUiState.Success) {
                scanBarcode(current.analysis.product.barcode)
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

    class Factory(private val repository: FoodAnalysisRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T {
            return MainViewModel(repository) as T
        }
    }
}
