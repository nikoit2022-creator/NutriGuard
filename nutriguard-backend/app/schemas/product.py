from pydantic import model_validator

from app.schemas.common import ORMModel


class ProductOut(ORMModel):
    """
    Mirrors com.example.data.model.ProductEntity exactly (API Contract 5.5).

    `ingredient_ids` is populated by the repository layer as a
    comma-separated string (matching the Room column) even though the
    database stores a normalized many-to-many relation internally.
    """

    barcode: str
    product_name: str
    brand: str
    category: str
    image_url: str | None = None
    raw_ingredient_text: str
    # Additive (code-review follow-up, task: "expose original ingredient
    # text and its source language through ProductOut using additive
    # camelCase fields and truthful missing values"). `raw_ingredient_text`
    # above stays the CANONICAL text (English/Bulgarian, possibly
    # translated -- the identity-bearing text
    # `app.services.ocr_normalizer.reconstruct_synthetic_ingredient`
    # re-tokenizes on every read). `original_ingredient_text` is the
    # TRUE pre-translation label/OCR text exactly as extracted, "" (never
    # `null`, matching the column's own NOT NULL default) when no
    # label/OCR text was ever captured for this product (e.g. a
    # barcode-only discovery) -- an empty string here is the honest
    # "nothing to show", not a missing/omitted value.
    original_ingredient_text: str = ""
    # Detected/declared language of `original_ingredient_text` ("en",
    # "bg", "en+bg", another code, "other", or "unknown") -- `null` (not
    # "") when genuinely never determined, matching the column's own
    # nullable default. See `app.services.language_detection.detect_language`.
    ingredient_text_source_language: str | None = None
    ingredient_ids: str
    # CONTRACT CHANGE (documented, README section 6 item 17): now
    # `int | None`, matching `FullProductAnalysisOut.health_score`.
    # `null` = "no Health Score available" -- the product is not
    # `isVerified` (nutrition and/or ingredient evidence incomplete).
    # Never a fabricated `0`: a genuine computed score of 0 is a real,
    # very poor score and is preserved as 0. Availability is decided by
    # the verified-data contract (`isVerified`), NOT by the numeric
    # value -- see `_health_score_only_when_verified`.
    health_score: int | None
    nova_group: int

    sugar_grams: float
    sodium_mg: float
    saturated_fat_grams: float
    nutrition_basis: str = "UNKNOWN"
    serving_size: float | None = None
    serving_unit: str | None = None
    has_artificial_sweeteners: bool
    has_preservatives: bool

    # CONTRACT CHANGE (documented, README section 6 item 17): TRI-STATE
    # `bool | None` (key always present, value may be `null`):
    #   null  = unknown / insufficient evidence -- show NOTHING (no badge,
    #           no claim, no warning),
    #   false = SUPPORTED incompatibility (positive evidence the product
    #           does not meet the requirement),
    #   true  = SUPPORTED suitability (an explicit provider/label claim).
    # `false` therefore no longer means "unknown"; the absence of a
    # keyword in (English-only) label text is never `true`. See
    # `app.services.dietary_suitability`.
    is_gluten_free: bool | None
    is_lactose_free: bool | None
    is_vegan: bool | None
    is_vegetarian: bool | None
    is_halal: bool | None
    is_kosher: bool | None

    # Comma-separated allergens POSITIVELY detected or declared for this
    # product. `""` means "none detected OR unknown" -- it is NOT a
    # confirmed allergen-free claim, and the literal string "None" is no
    # longer ever produced (legacy rows are migrated to ""). Shape/type
    # unchanged (`str`), so existing clients keep working; a client must
    # not present `""` as "allergen free".
    allergens_detected: str
    timestamp: int

    # Additive (V13, documented, see README "Deviations" section 6 item
    # 11 / section 11.14): these three were previously internal-only
    # (see `app/models/product.py`'s docstring), but a `200` success
    # response can now legitimately have `has_verified_nutrition=False`
    # (an ingredients-only label/OCR scan) -- the client needs a
    # reliable way to tell "no Health Score yet" (nutrition not
    # verified) apart from "a genuine score of a low value", and to
    # distinguish ingredient-recognition success from full
    # verification/health-score readiness. `is_verified` is exactly
    # `has_verified_nutrition AND has_verified_ingredients`, included
    # for convenience so the client never has to recompute it.
    has_verified_nutrition: bool
    has_verified_ingredients: bool
    is_verified: bool

    @model_validator(mode="after")
    def _health_score_only_when_verified(self) -> "ProductOut":
        """A Health Score exists only for a fully verified product
        (`is_verified` = verified nutrition AND verified ingredients --
        the same gate `food_analysis` scores behind). Defense in depth
        for the serializer: whatever a row happens to hold in its
        `health_score` column (a legacy `0` placeholder, a stale value),
        an unverified product is reported with `null`, on EVERY path that
        embeds a `ProductOut` (plain lookup, list, and the nested
        `product` of every scan response) -- the top-level scan
        `healthScore` was already `null` there. A genuine score of `0` on
        a verified product is untouched."""
        if not self.is_verified:
            self.health_score = None
        return self
