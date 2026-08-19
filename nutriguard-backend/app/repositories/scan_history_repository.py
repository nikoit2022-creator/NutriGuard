import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scan_history import ScanHistory


async def insert(db: AsyncSession, history: ScanHistory) -> ScanHistory:
    db.add(history)
    await db.flush()
    return history


async def get_for_user(
    db: AsyncSession, user_id: uuid.UUID, *, page: int, page_size: int
) -> tuple[list[ScanHistory], int]:
    stmt = (
        select(ScanHistory)
        .where(ScanHistory.user_id == user_id)
        .order_by(ScanHistory.scanned_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    count_stmt = select(func.count()).select_from(ScanHistory).where(ScanHistory.user_id == user_id)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt)).scalars().all()
    return list(rows), total


async def clear_for_user(db: AsyncSession, user_id: uuid.UUID) -> None:
    await db.execute(delete(ScanHistory).where(ScanHistory.user_id == user_id))
