from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.ingredient import IngredientOut
from app.schemas.product import ProductOut
from app.schemas.warning import HealthWarningOut


class BarcodeScanRequest(ORMModel):
    barcode: str = Field(min_length=1, max_length=64)


class OcrTextScanRequest(ORMModel):
    raw_text: str = Field(min_length=3)
    # Optional: lets a client that already attempted POST /scan/barcode
    # (and got `labelScanRequired`) resubmit the same barcode alongside
    # free-text label content so the backend can enrich/create the
    # canonical product for that barcode instead of a synthetic
    # `ocr_...` one. Omitted (or blank/a placeholder like "null") ->
    # behaves EXACTLY as before (see app.services.food_analysis.
    # analyze_ocr_text) -- fully backward compatible.
    barcode: str | None = Field(default=None, max_length=64)


class FullProductAnalysisOut(ORMModel):
    """Mirrors com.example.data.repository.FullProductAnalysis (API Contract 5.9)."""

    product: ProductOut
    ingredients: list[IngredientOut]
    health_score: int
    warnings: list[HealthWarningOut]
    is_from_database_cache: bool


class ProductDetailOut(ORMModel):
    """Response for GET /products/{barcode} (API Contract 6.4)."""

    product: ProductOut
    ingredients: list[IngredientOut]
