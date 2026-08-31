from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class Product(Base):
    """Mirrors com.example.data.model.ProductEntity"""

    __tablename__ = "products"

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_name: Mapped[str] = mapped_column("product_name", String(255), nullable=False, index=True)
    brand: Mapped[str] = mapped_column(String(128), nullable=False, default="", index=True)
    category: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    image_url: Mapped[str | None] = mapped_column("image_url", String(1024), nullable=True)
    raw_ingredient_text: Mapped[str] = mapped_column("raw_ingredient_text", Text, nullable=False, default="")
    # Comma-separated ingredient IDs, mirroring ProductEntity.ingredientIds exactly.
    # May reference synthetic (non-persisted) ingredient IDs created on-the-fly by the
    # OCR normalizer for tokens that don't match the scientific database
    # (API Contract 7.3) — intentionally NOT a foreign key for that reason.
    ingredient_ids: Mapped[str] = mapped_column("ingredient_ids", Text, nullable=False, default="")

    health_score: Mapped[int] = mapped_column("health_score", Integer, nullable=False)
    nova_group: Mapped[int] = mapped_column("nova_group", Integer, nullable=False)

    sugar_grams: Mapped[float] = mapped_column("sugar_grams", Numeric(6, 2), nullable=False, default=0)
    sodium_mg: Mapped[float] = mapped_column("sodium_mg", Numeric(8, 2), nullable=False, default=0)
    saturated_fat_grams: Mapped[float] = mapped_column(
        "saturated_fat_grams", Numeric(6, 2), nullable=False, default=0
    )
    has_artificial_sweeteners: Mapped[bool] = mapped_column(
        "has_artificial_sweeteners", Boolean, default=False
    )
    has_preservatives: Mapped[bool] = mapped_column("has_preservatives", Boolean, default=False)

    is_gluten_free: Mapped[bool] = mapped_column("is_gluten_free", Boolean, default=True)
    is_lactose_free: Mapped[bool] = mapped_column("is_lactose_free", Boolean, default=True)
    is_vegan: Mapped[bool] = mapped_column("is_vegan", Boolean, default=True)
    is_vegetarian: Mapped[bool] = mapped_column("is_vegetarian", Boolean, default=True)
    is_halal: Mapped[bool] = mapped_column("is_halal", Boolean, default=True)
    is_kosher: Mapped[bool] = mapped_column("is_kosher", Boolean, default=True)

    allergens_detected: Mapped[str] = mapped_column("allergens_detected", Text, nullable=False, default="None")
    timestamp: Mapped[int] = mapped_column(BigInteger, default=_now_ms)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # --- Discovery provenance (additive, backward compatible) ---------
    # Not part of the public API contract (ProductOut deliberately does
    # not expose these — see app/schemas/product.py); used only by
    # app/services/barcode_discovery.py and app/repositories/
    # product_repository.py to decide whether newly discovered data is
    # allowed to overwrite what's already stored for a barcode.
    #
    # "local"/"ocr"/"label_image" (this app's own analysis pipelines,
    # always is_verified=True) vs. a provider name ("open_food_facts",
    # "upcitemdb", ...) for a barcode-discovery result (is_verified
    # always False — see barcode_discovery.py's confidence model).
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="local", server_default="local")
    source_confidence: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
