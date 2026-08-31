import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProductSource(Base):
    """
    Provenance record for one (barcode, provider) discovery: what a
    single external source said about a barcode, kept separately from
    `Product` so conflicting/lower-confidence data from other sources
    stays reviewable instead of being silently overwritten or merged
    into the single persisted `Product` row. See
    `app/repositories/product_source_repository.py` and
    `app/services/barcode_discovery.py`.

    One row per (barcode, provider): a repeated discovery of the same
    barcode from the same provider refreshes this row in place rather
    than accumulating duplicates (dedup), while different providers for
    the same barcode each keep their own row (so a disagreement between
    two sources is visible, not lost).
    """

    __tablename__ = "product_sources"
    __table_args__ = (UniqueConstraint("barcode", "provider", name="uq_product_sources_barcode_provider"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    barcode: Mapped[str] = mapped_column(
        String(64), ForeignKey("products.barcode", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)

    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0)
    external_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    brand: Mapped[str | None] = mapped_column(String(128), nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    raw_ingredient_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Compact, curated JSON — never the full upstream response body.
    normalized_ingredients_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    nutrition_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    allergens: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # True once two-or-more sources disagreed on identity for this
    # barcode at discovery time (see barcode_discovery._flag_conflicts).
    # Kept per-row (not just on Product) so it's visible which specific
    # sources were in conflict.
    is_conflicting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether this row's data is what ended up written to `products` for
    # this barcode (the "winning" source at persist time).
    used_for_persisted_product: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
