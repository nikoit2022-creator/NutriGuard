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
    ingredient_ids: str
    health_score: int
    nova_group: int

    sugar_grams: float
    sodium_mg: float
    saturated_fat_grams: float
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
