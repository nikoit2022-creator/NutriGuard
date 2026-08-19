from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_id
from app.core.exceptions import ProductNotFoundError
from app.core.rate_limit import READ_RATE, limiter
from app.database.session import get_db
from app.repositories import product_repository
from app.schemas.common import Page
from app.schemas.product import ProductOut
from app.schemas.scan import ProductDetailOut
from app.services.food_analysis import fetch_ingredients_for_product

router = APIRouter(prefix="/products", tags=["products"])


def _to_product_out(product) -> ProductOut:
    return ProductOut.model_validate(product)


@router.get("", response_model=Page[ProductOut])
@limiter.limit(READ_RATE)
async def list_products(
    request: Request,
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
) -> Page[ProductOut]:
    """API Contract 6.5"""
    rows, total = await product_repository.search(db, query=search, page=page, page_size=pageSize)
    return Page[ProductOut](
        items=[_to_product_out(r) for r in rows],
        page=page,
        pageSize=pageSize,
        totalItems=total,
        totalPages=max(1, -(-total // pageSize)),
    )


@router.get("/{barcode}", response_model=ProductDetailOut)
@limiter.limit(READ_RATE)
async def get_product(
    request: Request,
    barcode: str,
    db: AsyncSession = Depends(get_db),
    _user_id: UUID = Depends(get_current_user_id),
) -> ProductDetailOut:
    """API Contract 6.4 — plain lookup, no warning/score recomputation."""
    product = await product_repository.get_by_barcode(db, barcode)
    if product is None:
        raise ProductNotFoundError(f"No product found for barcode {barcode}.")
    ingredients = await fetch_ingredients_for_product(db, product)
    return ProductDetailOut(product=_to_product_out(product), ingredients=ingredients)
