import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.enums import IngredientSource


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngredientAlias(Base):
    """
    A known name/spelling variant for exactly one canonical `Ingredient`
    row -- English, Bulgarian, a common misspelling, or an OCR
    misreading (see `app.services.ingredient_catalog`/
    `ingredient_normalization`). Deliberately a separate table rather
    than a column on `Ingredient` (see the task's own "avoid a parallel
    duplicate table unless proven necessary" instruction, and this
    class's justification): aliases are inherently many-to-one, need an
    exact/normalized-text lookup index independent of `Ingredient`'s own
    columns, and must be able to enforce -- at the database level, not
    just in application code -- that no two canonical ingredients ever
    claim the same normalized alias (the actual mechanism this task's
    "don't create separate records for citric acid / E330 / лимонена
    киселина" requirement is built on). A JSON/array column on
    `Ingredient` itself could not offer that per-alias uniqueness
    constraint (and SQLite, used by the test suite, has no native array
    type), so a dedicated table is the simpler and safer choice here,
    not the "parallel duplicate table" the task warns against -- there
    is exactly one ingredients table; this is its alias index.

    One row per (alias text, ingredient) -- `alias_normalized` is
    globally UNIQUE, so `get_or_create` (see
    `app/repositories/ingredient_alias_repository.py`) is the single
    source of truth resolving any known spelling straight to its
    canonical ingredient id.
    """

    __tablename__ = "ingredient_aliases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Original casing/spelling, as encountered or curated -- kept for
    # display/debugging; matching always goes through `alias_normalized`.
    alias_text: Mapped[str] = mapped_column(String(255), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # BCP-47-ish short code ("en", "bg", ...); null when the alias is a
    # spelling/OCR variant not tied to one specific language.
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    source: Mapped[IngredientSource] = mapped_column(
        Enum(IngredientSource, name="ingredient_source", native_enum=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
