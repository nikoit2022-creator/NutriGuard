from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.exceptions import IngredientNotFoundError
from app.core.rate_limit import READ_RATE, limiter
from app.database.session import get_db
from app.models.enums import RiskLevel
from app.repositories import ingredient_repository
from app.schemas.common import Page
from app.schemas.ingredient import IngredientOut

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


@router.get("", response_model=Page[IngredientOut])
@limiter.limit(READ_RATE)
async def list_ingredients(
    request: Request,
    search: str | None = Query(default=None),
    riskLevel: RiskLevel | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
) -> Page[IngredientOut]:
    """API Contract 6.6"""
    rows, total = await ingredient_repository.search(
        db, query=search, risk_level=riskLevel, page=page, page_size=pageSize
    )
    return Page[IngredientOut](
        items=[IngredientOut.model_validate(r) for r in rows],
        page=page,
        pageSize=pageSize,
        totalItems=total,
        totalPages=max(1, -(-total // pageSize)),
    )


@router.get("/{ingredient_id}", response_model=IngredientOut)
@limiter.limit(READ_RATE)
async def get_ingredient(
    request: Request,
    ingredient_id: str,
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
) -> IngredientOut:
    """API Contract 6.7 — lookup by id OR e-number."""
    ingredient = await ingredient_repository.get_by_id_or_e_number(db, ingredient_id)
    if ingredient is None:
        raise IngredientNotFoundError(f"No ingredient found for id/eNumber '{ingredient_id}'.")
    return IngredientOut.model_validate(ingredient)
