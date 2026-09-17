import time
from typing import Any
from uuid import UUID

import structlog
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

_logger = structlog.get_logger(__name__)


def _safe_record_scan_diagnostic(**fields: Any) -> None:
    """Local defense-in-depth on top of `record_scan_diagnostic`'s own
    internal best-effort guarantee (task: "diagnostic failures must
    never break scanning"). `record_scan_diagnostic` already swallows
    every exception itself today, but this call site must not depend
    solely on that implementation detail never regressing -- a
    diagnostics failure must never replace a real response/error with
    an unrelated exception, at every one of THIS router's own call
    sites, independent of what `app.core.scan_diagnostics` does
    internally."""
    try:
        record_scan_diagnostic(**fields)
    except Exception:  # noqa: BLE001
        _logger.warning("scan_diagnostic_write_failed")


_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


def _to_analysis_out(result: dict) -> FullProductAnalysisOut:
    return FullProductAnalysisOut(
        product=result["product"],
        ingredients=result["ingredients"],
        health_score=result["health_score"],
        warnings=result["warnings"],
        is_from_database_cache=result["is_from_database_cache"],
    )


def _ingredient_language_counts(ingredients: list[Any]) -> dict:
    """Recognized/unresolved/untranslated ingredient counts (task:
    "recognized, unresolved, and untranslated ingredient counts") --
    computed from what `app.services.ingredient_catalog` actually
    persisted for THIS scan, never guessed. `unresolved` is every
    ingredient flagged `identity_uncertain` for ANY reason (suspected
    OCR concatenation, ambiguous segmentation, or an unreliable
    translation attempt); `untranslated` is specifically the subset
    where a translation was attempted but could not be verified (see
    `app.services.ingredient_translation`).
    """
    unresolved = 0
    untranslated = 0
    for ing in ingredients:
        if getattr(ing, "identity_uncertain", False):
            unresolved += 1
            if getattr(ing, "uncertainty_reason", None) == "TRANSLATION_UNRELIABLE":
                untranslated += 1
    return {
        "recognizedIngredientCount": len(ingredients),
        "unresolvedIngredientCount": unresolved,
        "untranslatedIngredientCount": untranslated,
    }


def _diagnostic_base(request: Request, *, operation: str, data_source: str, barcode: str | None) -> dict:
    return {
        "requestId": getattr(request.state, "request_id", None),
        "operation": operation,
        "dataSource": data_source,
        "barcode": barcode,
        # Coarse, honest stages this router layer actually observes --
        # never a guess at what happened deeper inside
        # `app.services.food_analysis`'s own pipeline, which this
        # endpoint layer does not instrument (task: "do not invent stage
        # information that was not observed").
        "stage": "request_validated",
    }


@router.post("/barcode", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_barcode(
    request: Request,
    body: BarcodeScanRequest,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> FullProductAnalysisOut:
    """API Contract 6.1"""
    started = time.perf_counter()
    if not body.barcode.strip():
        raise ValidationAppError("barcode must not be empty.")

    diagnostic_base = _diagnostic_base(
        request, operation="scan_barcode", data_source="barcode", barcode=body.barcode.strip()
    )
    try:
        result = await food_analysis.analyze_barcode(db, user_id, body.barcode.strip())
        diagnostic_base["stage"] = "analysis_complete"
        out = _to_analysis_out(result)
        diagnostic_base["stage"] = "response_built"
    except AppError as exc:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    _safe_record_scan_diagnostic(
        **diagnostic_base,
        outcome="success",
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out


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
    started = time.perf_counter()
    if len(body.raw_text.strip()) < 3:
        raise ValidationAppError("rawText must be at least 3 characters.")

    barcode = clean_optional(body.barcode)
    diagnostic_base = _diagnostic_base(
        request, operation="scan_ocr_text", data_source="ocr_text", barcode=barcode
    )
    try:
        if barcode:
            result = await food_analysis.analyze_ocr_text_with_barcode(
                db, user_id, body.raw_text.strip(), barcode
            )
        else:
            result = await food_analysis.analyze_ocr_text(db, user_id, body.raw_text.strip())
        diagnostic_base["stage"] = "analysis_complete"
        out = _to_analysis_out(result)
        diagnostic_base["stage"] = "response_built"
    except ProductNotFoundError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        _safe_record_scan_diagnostic(
            **{**diagnostic_base, "barcode": barcode},
            outcome="partial" if details.get("labelScanRequired") else "failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except AppError as exc:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    product = result["product"]
    _safe_record_scan_diagnostic(
        **{**diagnostic_base, "barcode": product.barcode},
        outcome="success",
        detectedLanguage=getattr(product, "ingredient_text_source_language", None),
        translationUsed=product.source == "label_scan_translated",
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out


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
    started = time.perf_counter()
    cleaned_barcode = clean_optional(barcode)
    # Early validation failures (bad content-type, oversized image) now
    # get a diagnostic record too -- previously these raised before
    # `diagnostic_base` was even built, so the diagnostics journal
    # silently had no coverage of them at all.
    diagnostic_base = _diagnostic_base(
        request, operation="scan_label_image", data_source="label_image", barcode=cleaned_barcode
    )
    diagnostic_base["stage"] = "content_type_validated"

    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="VALIDATION_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise ValidationAppError(f"Unsupported image type: {image.content_type}")

    contents = await image.read()
    diagnostic_base["stage"] = "image_read"
    if len(contents) > settings.MAX_IMAGE_SIZE_BYTES:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="IMAGE_TOO_LARGE",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise ImageTooLargeError(
            f"Image exceeds the {settings.MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB limit."
        )

    try:
        if cleaned_barcode:
            result = await food_analysis.analyze_label_image_with_barcode(
                db, user_id, contents, cleaned_barcode
            )
        else:
            result = await food_analysis.analyze_label_image(db, user_id, contents)
        diagnostic_base["stage"] = "analysis_complete"
        # Response construction/serialization happens INSIDE this try
        # block now, not after it: a "success" record must only ever be
        # written once the response actually exists (task: "record
        # success only after response construction/serialization
        # succeeds") -- a `FullProductAnalysisOut` validation error here
        # now correctly falls into the generic `except Exception` below
        # instead of leaving a false "success" diagnostic line behind.
        out = _to_analysis_out(result)
        diagnostic_base["stage"] = "response_built"
    except ProductNotFoundError as exc:
        details = exc.details if isinstance(exc.details, dict) else {}
        ingredients = details.get("ingredients")
        _safe_record_scan_diagnostic(
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
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode=exc.code,
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise
    except Exception:
        _safe_record_scan_diagnostic(
            **diagnostic_base,
            outcome="failed",
            errorCode="INTERNAL_ERROR",
            durationMs=round((time.perf_counter() - started) * 1000, 2),
        )
        raise

    product = result["product"]
    _safe_record_scan_diagnostic(
        **{**diagnostic_base, "barcode": product.barcode},
        outcome="success",
        nutritionRecognized=product.has_verified_nutrition,
        ingredientsRecognized=product.has_verified_ingredients,
        nutritionBasis=product.nutrition_basis,
        detectedLanguage=getattr(product, "ingredient_text_source_language", None),
        translationUsed=product.source == "label_scan_translated",
        **_ingredient_language_counts(result["ingredients"]),
        durationMs=round((time.perf_counter() - started) * 1000, 2),
    )
    return out
