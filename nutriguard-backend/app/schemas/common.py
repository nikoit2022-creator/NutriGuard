from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

T = TypeVar("T")


class ORMModel(BaseModel):
    """
    Base class for schemas that read directly from snake_case ORM
    attributes but must serialize as camelCase JSON, to match the
    Kotlin `data class` field names 1:1 (API Contract section 0/5).
    """
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


class Page(BaseModel, Generic[T]):
    """Generic pagination envelope (API Contract section 4.3)."""
    items: List[T]
    page: int
    pageSize: int
    totalItems: int
    totalPages: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None
    timestamp: int


class ErrorResponse(BaseModel):
    error: ErrorBody
