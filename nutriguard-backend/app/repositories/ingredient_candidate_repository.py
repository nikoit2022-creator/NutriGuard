"""
DB access for `IngredientCandidate` (issue #23, stage 2). No policy lives
here (what counts as junk, what to flag, the row cap): only how to read,
insert-or-update and delete candidate rows safely under concurrency.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ingredient_candidate import IngredientCandidate


@dataclass(frozen=True)
class ObservationOutcome:
    """`inserted`: a new row was created; `updated`: an existing row's
    counter was raised; `dropped`: the row cap stopped a NEW insert."""

    inserted: bool = False
    updated: bool = False
    dropped: bool = False


async def get_by_key(db: AsyncSession, key: str, *, for_update: bool = False) -> IngredientCandidate | None:
    stmt = select(IngredientCandidate).where(IngredientCandidate.normalized_key == key)
    if for_update:
        # A real row lock on PostgreSQL (two concurrent scans of the same
        # token serialize here instead of losing one increment or one flag
        # merge); SQLite accepts and ignores it.
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(IngredientCandidate))).scalar_one()


def _touch(
    row: IngredientCandidate,
    *,
    now: datetime,
    e_number: str | None,
    ingredient_id: str | None,
    flags: str,
    status: str,
    merge_flags,
) -> None:
    """Applies one more observation to a LOCKED row. The counter is raised
    on the locked row (the lock, not the read value, makes it safe). Links
    and identifiers are only ever FILLED, never overwritten, so a later
    observation cannot silently repoint a candidate at another identity."""
    row.encounter_count = row.encounter_count + 1
    row.last_seen_at = now
    if row.e_number is None and e_number:
        row.e_number = e_number
    if row.ingredient_id is None and ingredient_id:
        row.ingredient_id = ingredient_id
    merged_flags, merged_status = merge_flags(row.flags, flags)
    row.flags = merged_flags
    row.status = merged_status


async def observe(
    db: AsyncSession,
    *,
    key: str,
    display_name: str,
    e_number: str | None,
    ingredient_id: str | None,
    flags: str,
    status: str,
    now: datetime,
    max_rows: int,
    merge_flags,
) -> ObservationOutcome:
    """Insert-or-count one observation of `key`.

    Existing row: lock it, raise `encounter_count`, refresh `last_seen_at`,
    fill identifier/link only when empty, union the flags (`merge_flags`
    returns `(flags, status)`). New row: refuse when `max_rows` is reached
    (never evicts), otherwise insert inside a SAVEPOINT so losing a race
    on the unique key only undoes this insert, then fall through to the
    locked-update path. Never commits."""
    existing = await get_by_key(db, key, for_update=True)
    if existing is not None:
        _touch(existing, now=now, e_number=e_number, ingredient_id=ingredient_id, flags=flags, status=status,
               merge_flags=merge_flags)
        await db.flush()
        return ObservationOutcome(updated=True)

    if await count(db) >= max_rows:
        return ObservationOutcome(dropped=True)

    row = IngredientCandidate(
        normalized_key=key,
        display_name=display_name,
        e_number=e_number,
        ingredient_id=ingredient_id,
        status=status,
        flags=flags,
        encounter_count=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    try:
        async with db.begin_nested():
            db.add(row)
            await db.flush()
        return ObservationOutcome(inserted=True)
    except IntegrityError:
        existing = await get_by_key(db, key, for_update=True)
        if existing is None:
            raise
        _touch(existing, now=now, e_number=e_number, ingredient_id=ingredient_id, flags=flags, status=status,
               merge_flags=merge_flags)
        await db.flush()
        return ObservationOutcome(updated=True)


async def list_prunable(
    db: AsyncSession, *, unseen_since: datetime, max_encounters: int, limit: int
) -> list[IngredientCandidate]:
    """Rows a manual `prune` may remove: not seen since `unseen_since`,
    seen at most `max_encounters` times, and never FLAGGED (awaiting review)."""
    stmt = (
        select(IngredientCandidate)
        .where(
            IngredientCandidate.last_seen_at < unseen_since,
            IngredientCandidate.encounter_count <= max_encounters,
            IngredientCandidate.status != "FLAGGED",
        )
        .order_by(IngredientCandidate.last_seen_at.asc(), IngredientCandidate.id.asc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def delete_by_ids(db: AsyncSession, ids: list[int]) -> int:
    if not ids:
        return 0
    result = await db.execute(delete(IngredientCandidate).where(IngredientCandidate.id.in_(ids)))
    await db.flush()
    return result.rowcount or 0
