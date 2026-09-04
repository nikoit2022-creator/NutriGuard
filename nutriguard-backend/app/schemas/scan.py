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
    """Mirrors com.example.data.repository.FullProductAnalysis (API Contract 5.9).

    CONTRACT CHANGE (V13, documented, see README "Deviations" section 6
    item 11 / section 11.14): `health_score` is now `int | None` instead
    of always `int`. A label-driven scan (`/scan/label-image`,
    `/scan/ocr-text`, and their barcode-linked variants) can return a
    normal `200` success once ingredient recognition succeeds, even when
    nutrition extraction is missing/incomplete -- there is then no real
    Health Score to report, and `null` is returned rather than a
    fabricated placeholder (never `0`, which would read as a real, very
    poor score). `/scan/barcode` is unaffected: it still only ever
    returns a genuine, non-null score (see `food_analysis.analyze_barcode`).
    """

    product: ProductOut
    ingredients: list[IngredientOut]
    health_score: int | None
    warnings: list[HealthWarningOut]
    is_from_database_cache: bool


class ProductDetailOut(ORMModel):
    """Response for GET /products/{barcode} (API Contract 6.4)."""

    product: ProductOut
    ingredients: list[IngredientOut]
