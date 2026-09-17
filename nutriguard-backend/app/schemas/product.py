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
    health_score: int
    nova_group: int

    sugar_grams: float
    sodium_mg: float
    saturated_fat_grams: float
    nutrition_basis: str = "UNKNOWN"
    serving_size: float | None = None
    serving_unit: str | None = None
    has_artificial_sweeteners: bool
    has_preservatives: bool

    is_gluten_free: bool
    is_lactose_free: bool
    is_vegan: bool
    is_vegetarian: bool
    is_halal: bool
    is_kosher: bool

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
