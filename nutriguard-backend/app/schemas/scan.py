from pydantic import Field

from app.schemas.common import ORMModel
from app.schemas.ingredient import IngredientOut
from app.schemas.product import ProductOut
from app.schemas.warning import HealthWarningOut


class BarcodeScanRequest(ORMModel):
    barcode: str = Field(min_length=1, max_length=64)


class OcrTextScanRequest(ORMModel):
    raw_text: str = Field(min_length=3)


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
