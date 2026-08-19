from app.schemas.common import ORMModel


class HealthProfileIn(ORMModel):
    """Mirrors com.example.data.model.UserHealthProfile (API Contract 5.7). No `id` field:
    the server derives the owning user from the access token (see 6.9)."""

    has_diabetes: bool = False
    has_hypertension: bool = False
    has_kidney_disease: bool = False
    has_gout: bool = False
    is_pregnant: bool = False
    for_children: bool = False
    has_high_cholesterol: bool = False

    avoid_gluten: bool = False
    avoid_lactose: bool = False
    avoid_peanuts: bool = False
    avoid_soy: bool = False
    avoid_tree_nuts: bool = False
    require_vegan: bool = False
    require_vegetarian: bool = False
    require_halal: bool = False
    require_kosher: bool = False


class HealthProfileOut(HealthProfileIn):
    """Same shape as the client's UserHealthProfile, plus the legacy `id` field
    kept at a constant value of 1 for backward-compatible deserialization
    (API Contract 5.7)."""

    id: int = 1
