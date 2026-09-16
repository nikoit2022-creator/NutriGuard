from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.models.enums import IngredientTranslationSource, IngredientTranslationStatus


class IngredientLocalization(Base):
    """Reviewed localized display text for one canonical ingredient.

    Scientific identity, risk/approval enums, numeric ADI values and
    citations/URLs remain on :class:`Ingredient`; this table only holds
    user-facing prose.  The source-content hash prevents an old
    translation from being served after its canonical English source
    has changed.
    """

    __tablename__ = "ingredient_localizations"

    ingredient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ingredients.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(2), primary_key=True)
    common_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    purpose_in_food: Mapped[str] = mapped_column(Text, nullable=False, default="")
    health_concerns: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_level: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    countries_restricted_or_banned: Mapped[str] = mapped_column(Text, nullable=False, default="")
    efsa_status: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fda_status: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    acceptable_daily_intake: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    side_effects: Mapped[str] = mapped_column(Text, nullable=False, default="")
    allergens: Mapped[str] = mapped_column(Text, nullable=False, default="")
    translation_status: Mapped[IngredientTranslationStatus] = mapped_column(
        Enum(IngredientTranslationStatus, name="ingredient_translation_status", native_enum=True), nullable=False
    )
    translation_source: Mapped[IngredientTranslationSource] = mapped_column(
        Enum(IngredientTranslationSource, name="ingredient_translation_source", native_enum=True), nullable=False
    )
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    ingredient: Mapped["Ingredient"] = relationship(back_populates="localization_rows")  # type: ignore[name-defined]
