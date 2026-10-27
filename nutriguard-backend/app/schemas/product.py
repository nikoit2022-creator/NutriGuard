import html

from pydantic import Field, ValidationInfo, computed_field, field_validator

from app.schemas.common import ORMModel
from app.services.product_label import dietary_statuses, split_ingredients_and_traces, stated_allergens


class ProductOut(ORMModel):
    """
    Mirrors the legacy ProductEntity fields and adds evidence-aware display
    fields (`possibleTraces`, `dietaryStatuses`, `source`).

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

    # Additive display evidence. Legacy booleans remain on the wire for old
    # clients, while the new UI uses these truthful three-state values.
    source: str = "local"
    dietary_evidence_json: str = Field(default="", exclude=True)

    @field_validator("product_name", "brand", "category", mode="before")
    @classmethod
    def decode_legacy_html_entities(cls, value: str) -> str:
        return html.unescape(value) if isinstance(value, str) else value

    @field_validator("category", mode="after")
    @classmethod
    def hide_unsupported_category_script(cls, value: str) -> str:
        # Ukrainian-specific characters are outside Bulgarian orthography;
        # do not present an untranslated taxonomy label as local UI copy.
        return "General Food" if any(ch in value for ch in "іїєґІЇЄҐ") else value

    @field_validator("allergens_detected", mode="after")
    @classmethod
    def include_named_ingredient_allergens(cls, value: str, info: ValidationInfo) -> str:
        stored = {part.strip() for part in value.split(",") if part.strip() and part.strip().lower() != "none"}
        named = stated_allergens(info.data.get("raw_ingredient_text", ""))
        return ", ".join(sorted(stored | named))

    @computed_field
    @property
    def possible_traces(self) -> str:
        return split_ingredients_and_traces(self.raw_ingredient_text)[1]

    @computed_field
    @property
    def dietary_statuses(self) -> dict[str, str]:
        return dietary_statuses(self)
