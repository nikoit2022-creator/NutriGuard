import time
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.exceptions import AppError, ImageTooLargeError, ProductNotFoundError, ValidationAppError
from app.core.rate_limit import SCAN_RATE, limiter
from app.core.scan_diagnostics import record_scan_diagnostic
from app.database.session import get_db
from app.schemas.scan import BarcodeScanRequest, FullProductAnalysisOut, OcrTextScanRequest
from app.services import food_analysis
from app.services.barcode_text_safety import clean_optional

router = APIRouter(prefix="/scan", tags=["scan"])

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


def _to_analysis_out(result: dict) -> FullProductAnalysisOut:
    return FullProductAnalysisOut(
        product=result["product"],
        ingredients=result["ingredients"],
        health_score=result["health_score"],
        warnings=result["warnings"],
        is_from_database_cache=result["is_from_database_cache"],
    )


@router.post("/barcode", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_barcode(
    request: Request,
    body: BarcodeScanRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> FullProductAnalysisOut:
    """API Contract 6.1"""
    if not body.barcode.strip():
        raise ValidationAppError("barcode must not be empty.")
    result = await food_analysis.analyze_barcode(db, user_id, body.barcode.strip())
    return _to_analysis_out(result)


@router.post("/ocr-text", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_ocr_text(
    request: Request,
    body: OcrTextScanRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> FullProductAnalysisOut:
    """API Contract 6.2. `body.barcode` is optional (see `OcrTextScanRequest`
    docstring) -- when supplied it routes through the same barcode
    enrichment/upsert pipeline `/scan/label-image` uses; omitted, it
    behaves exactly as before."""
    if len(body.raw_text.strip()) < 3:
        raise ValidationAppError("rawText must be at least 3 characters.")

    barcode = clean_optional(body.barcode)
    if barcode:
        result = await food_analysis.analyze_ocr_text_with_barcode(
            db, user_id, body.raw_text.strip(), barcode
        )
    else:
        result = await food_analysis.analyze_ocr_text(db, user_id, body.raw_text.strip())
    return _to_analysis_out(result)


@router.post("/label-image", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_label_image(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    image: UploadFile = File(...),
    barcode: str | None = Form(default=None),
) -> FullProductAnalysisOut:
    """
    API Contract 6.3, extended with an optional multipart `barcode`
    field: lets a client that already attempted POST /scan/barcode (and
    got `labelScanRequired`) resubmit the same barcode alongside the
    label image, so the backend can combine both sources into one
    canonical, persisted product instead of a synthetic `img_...` one.

    `barcode` omitted, blank, or a literal placeholder (e.g. "null",
    "N/A") -> behaves EXACTLY as before (`food_analysis.
    analyze_label_image`), fully backward compatible with existing
    clients. A supplied barcode is validated/canonicalized through the
    same `barcode_validation` module `/scan/barcode` uses; an invalid
    one returns the standard structured `VALIDATION_ERROR` response.
    """
    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        raise ValidationAppError(f"Unsupported image type: {image.content_type}")

    contents = await image.read()
    if len(contents) > settings.MAX_IMAGE_SIZE_BYTES:
        raise ImageTooLargeError(
            f"Image exceeds the {settings.MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB limit."
        )

    cleaned_barcode = clean_optional(barcode)
    started = time.perf_counter()
    diagnostic_base = {
        "requestId": getattr(request.state, "request_id", None),
        "scanType": "LABEL_IMAGE",
        "barcode": cleaned_barcode,
        "stage": "label_analysis",
    }
    try:
        if cleaned_barcode:
            result = await food_analysis.analyze_label_image_with_barcode(
                db, user_id, contents, cleaned_barcode
            )
        else:
            result = await food_analysis.analyze_label_image(db, user_id, contents)
    except ProductNotFoundError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        ingredients = details.get("ingredients")
        record_scan_diagnostic(
            **diagnostic_base,
            outcome="partial" if details.get("labelScanRequired") else "failed",
            errorCode=exc.code,
            nutritionRecognized=not bool(details.get("nutritionScanRequired", True)),
            ingredientsRecognized=not bool(details.get("ingredientsScanRequired", True)),
            recognizedIngredientCount=len(ingredients) if isinstance(ingredients, list) else 0,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except AppError as exc:
        record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    product = result["product"]
    record_scan_diagnostic(
        **{**diagnostic_base, "barcode": product.barcode},
        outcome="success",
        nutritionRecognized=product.has_verified_nutrition,
        ingredientsRecognized=product.has_verified_ingredients,
        recognizedIngredientCount=len(result["ingredients"]),
        nutritionBasis=product.nutrition_basis,
        translationUsed=product.source == "label_scan_translated",
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return _to_analysis_out(result)
