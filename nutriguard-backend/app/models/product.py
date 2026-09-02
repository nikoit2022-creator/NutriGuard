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
    # "local"/"ocr"/"label_image" (this app's own analysis pipelines) vs.
    # a provider name ("open_food_facts", "upcitemdb", ...) for a
    # barcode-discovery result.
    #
    # `is_verified`/`has_verified_nutrition`/`has_verified_ingredients`
    # (V12, PR #9 review round 5, finding 2) all DEFAULT TO FALSE. This
    # is a deliberate reversal of the ORIGINAL (V6/V11) design, which
    # defaulted `is_verified`/`has_verified_nutrition` to TRUE on the
    # theory that every row not explicitly created by an external
    # discovery must be a genuinely verified local one -- reviewed and
    # found unsafe: it meant ANY future write path (a new call site, a
    # refactor, a bug) that simply forgot to pass one of these
    # arguments would silently create FULLY VERIFIED, Health-Score-
    # eligible evidence rather than failing safe. Every current write
    # path already passes all three explicitly (see
    # `food_analysis._to_product_model` and every one of its callers,
    # `_apply_discovered_fields`, `_apply_label_enrichment`) -- the
    # default is deliberately never exercised by this app's own code,
    # and now exists ONLY as a safety net: an insert that omits one of
    # these columns produces an UNVERIFIED row (no Health Score, gated
    # behind `labelScanRequired` on every read), the safe direction to
    # fail in, never the other way around.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="local", server_default="local")
    source_confidence: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # has_verified_nutrition / has_verified_ingredients (V11, PR #9
    # review round 4): TWO INDEPENDENT evidence groups, deliberately
    # split rather than one flag conflating both. A product's nutrition
    # and its ingredient list are physically different parts of a label
    # (the nutrition facts panel vs. the ingredients list) and are
    # legitimately learned from DIFFERENT sources at DIFFERENT times --
    # e.g. a trusted barcode provider states real per-100g nutrition but
    # no ingredients_text, and the user's own later ingredient-list
    # photo supplies the other half. Tracking them separately lets each
    # piece of trusted evidence be preserved and COUNTED, regardless of
    # which request supplied it, instead of requiring both in the same
    # request (see `app/services/food_analysis.py`'s "Barcode + label
    # enrichment" section and README section 11.2/11.8).
    #
    # has_verified_nutrition: the three core numeric Health Score inputs
    # (sugar/sodium/saturated-fat) are genuinely, trustworthily known --
    # never a heuristic guess or a placeholder. Gates nothing on its
    # own; see `is_verified` above for the actual Health Score gate.
    # Defaults to False (V12) -- see the fail-safe rationale above.
    has_verified_nutrition: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # has_verified_ingredients: usable ingredient-list evidence
    # (raw_ingredient_text / ingredient_ids, and the NOVA group and
    # dietary/allergen flags derived from it) is genuinely, trustworthily
    # known -- from a trusted provider's `ingredients_text`, or a
    # successful structured Gemini label-image extraction, or literal
    # user-submitted OCR text -- never a heuristic guess from a
    # placeholder/error string. Defaults to False (V12) -- see the
    # fail-safe rationale above.
    has_verified_ingredients: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # is_verified: BOTH evidence groups are true -- the single gate for
    # "safe to compute a real Health Score for" and "protected from
    # being overwritten by a future lower-trust discovery/enrichment
    # attempt". Every write path in `app/services/food_analysis.py`
    # keeps this in sync as `has_verified_nutrition AND
    # has_verified_ingredients` -- it is never set independently.
    # Gates BOTH the initial discovery/enrichment response and every
    # later cache-hit lookup of the same row (see
    # `food_analysis.analyze_barcode` / `_finalize_barcode_enrichment`)
    # -- so an incomplete cached product can never accidentally be
    # scored on a repeat scan either. Always True for local/OCR/
    # label-image products with genuinely complete data, which always
    # run the real scoring pipeline against actual label content.
