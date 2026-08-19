from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.exceptions import ImageTooLargeError, ValidationAppError
from app.core.rate_limit import SCAN_RATE, limiter
from app.database.session import get_db
from app.schemas.scan import BarcodeScanRequest, FullProductAnalysisOut, OcrTextScanRequest
from app.services import food_analysis

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
    """API Contract 6.2"""
    if len(body.raw_text.strip()) < 3:
        raise ValidationAppError("rawText must be at least 3 characters.")
    result = await food_analysis.analyze_ocr_text(db, user_id, body.raw_text.strip())
    return _to_analysis_out(result)


@router.post("/label-image", response_model=FullProductAnalysisOut)
@limiter.limit(SCAN_RATE)
async def scan_label_image(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
    image: UploadFile = File(...),
) -> FullProductAnalysisOut:
    """API Contract 6.3"""
    if image.content_type not in _ALLOWED_IMAGE_TYPES:
        raise ValidationAppError(f"Unsupported image type: {image.content_type}")

    contents = await image.read()
    if len(contents) > settings.MAX_IMAGE_SIZE_BYTES:
        raise ImageTooLargeError(
            f"Image exceeds the {settings.MAX_IMAGE_SIZE_BYTES // (1024 * 1024)}MB limit."
        )

    result = await food_analysis.analyze_label_image(db, user_id, contents)
    return _to_analysis_out(result)
