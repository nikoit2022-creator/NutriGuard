from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
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
    effect_conditions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    dietary_guidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    translation_status: Mapped[IngredientTranslationStatus] = mapped_column(
        Enum(IngredientTranslationStatus, name="ingredient_translation_status", native_enum=True), nullable=False
    )
    translation_source: Mapped[IngredientTranslationSource] = mapped_column(
        Enum(IngredientTranslationSource, name="ingredient_translation_source", native_enum=True), nullable=False
    )
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md: a narrowly scoped,
    # explicit owner-approved-publication escape hatch -- distinct from
    # `translation_status`, never a replacement for it. A row may be
    # served to consumers while still honestly `DRAFT` (no human
    # translator has reviewed it) when an owner has explicitly approved
    # THIS content for display without that review (e.g. the bounded
    # OpenFoodTox pilot). Defaults to `False` for every row that
    # predates this column and every row any other writer (load_seed,
    # a future translation-review workflow) ever creates -- only
    # `app/seed/load_openfoodtox_pilot_content.py`'s narrow,
    # allowlisted import sets this `True`, and only alongside the
    # honest `DRAFT`/`MACHINE_TRANSLATED` status/source pair, never
    # `REVIEWED`/`HUMAN_CURATED` (which would be a false human-review
    # claim). See `app.services.ingredient_localization.build_localizations`
    # for the exact serving rule this flag participates in.
    owner_approved_without_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    ingredient: Mapped["Ingredient"] = relationship(back_populates="localization_rows")  # type: ignore[name-defined]
