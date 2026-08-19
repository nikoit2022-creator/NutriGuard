from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.rate_limit import READ_RATE, limiter
from app.database.session import get_db
from app.repositories import scan_history_repository
from app.schemas.common import Page
from app.schemas.scan_history import ScanHistoryOut

router = APIRouter(prefix="/scan-history", tags=["scan-history"])


@router.get("", response_model=Page[ScanHistoryOut])
@limiter.limit(READ_RATE)
async def list_scan_history(
    request: Request,
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Page[ScanHistoryOut]:
    """API Contract 6.10"""
    rows, total = await scan_history_repository.get_for_user(db, user_id, page=page, page_size=pageSize)
    return Page[ScanHistoryOut](
        items=[ScanHistoryOut.model_validate(r) for r in rows],
        page=page,
        pageSize=pageSize,
        totalItems=total,
        totalPages=max(1, -(-total // pageSize)),
    )


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(READ_RATE)
async def clear_scan_history(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
) -> Response:
    """API Contract 6.11"""
    await scan_history_repository.clear_for_user(db, user_id)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
