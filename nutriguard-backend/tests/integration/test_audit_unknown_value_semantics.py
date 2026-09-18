"""
Reproduction tests for GitHub issue #21, section 1 (unknown-value/API
semantics audit). These are INTENTIONAL REPRODUCTIONS of confirmed
ambiguities, added by an audit task -- not a claim about desired
behavior, and not a modification of any existing test. Report:
nutriguard-backend/docs/BACKEND_DATA_QUALITY_AUDIT.md.
"""
from datetime import datetime, timezone

import pytest

from app.models.product import Product


async def _register_device(client, device_id="audit-unknown-value-device"):
    resp = await client.post("/api/v1/auth/device", json={"deviceId": device_id})
    token = resp.json()["accessToken"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_unverified_product_health_score_placeholder_is_returned_as_a_real_zero(
    app_client, db_session
):
    """GET /products/{barcode} performs "no warning/score recomputation"
    (app/api/v1/products.py:42-53's own docstring) and ProductOut.health_score
    is a required, non-nullable `int` (app/schemas/product.py:38). A product
    created via `_new_product_from_label` (app/services/food_analysis.py:223)
    is inserted with the literal placeholder `health_score=0`, commented there
    as "recomputed live on every read" -- but the plain GET path never
    recomputes it. For a product that is not (yet) verified, GET returns
    `healthScore: 0` on the wire, indistinguishable from a genuine
    worst-possible score, unless the client separately cross-checks
    `isVerified`/`hasVerifiedNutrition` (nothing in the schema enforces that)."""
    headers = await _register_device(app_client)

    product = Product(
        barcode="9990000000001",
        product_name="Audit Unverified Product",
        brand="Audit",
        category="Test",
        raw_ingredient_text="water",
        ingredient_ids="",
        health_score=0,  # the exact placeholder written by _new_product_from_label
        nova_group=1,
        nutrition_basis="UNKNOWN",
        source="local",
        is_verified=False,
        has_verified_nutrition=False,
        has_verified_ingredients=False,
        discovered_at=None,
        last_verified_at=datetime.now(timezone.utc),
    )
    db_session.add(product)
    await db_session.commit()

    resp = await app_client.get(f"/api/v1/products/{product.barcode}", headers=headers)
    assert resp.status_code == 200
    body = resp.json()["product"]

    # Confirmed ambiguity: a never-scored product reports the same
    # `healthScore: 0` a genuinely-assessed worst-possible product would.
    assert body["healthScore"] == 0
    assert body["isVerified"] is False
    assert body["hasVerifiedNutrition"] is False


@pytest.mark.asyncio
async def test_fallback_heuristic_writes_literal_none_string_for_unmatched_allergens():
    """app/services/fallback_analysis.py:91 writes the literal string
    "None" for `allergens_detected` whenever its 2-keyword ("soy"/"milk")
    substring heuristic finds neither -- egg, peanut, tree nut, gluten,
    sesame, fish, shellfish, and sulphite allergens are never checked.
    Elsewhere in this codebase (app/services/gemini_image_parser.py:429,
    app/services/food_analysis.py:1058-1067's `_is_meaningful_identity`/
    `barcode_text_safety.is_placeholder`) the literal string "None" is
    explicitly treated as a non-meaningful placeholder that must never be
    read as a real confirmed-absence claim -- but `ProductOut.allergens_detected`
    (app/schemas/product.py:57) is a plain `str` with no such guard applied
    at serialization, so a client receives the bare string "None" with
    nothing distinguishing it from a real, confirmed allergen-free result."""
    from app.services.fallback_analysis import fallback_local_analysis

    data, _ingredients = fallback_local_analysis(
        title="Mystery Snack",
        raw_text="wheat flour, palm oil, salt",  # contains a real, unchecked allergen (gluten)
        db_ingredients=[],
    )

    assert data.allergens_detected == "None"
    # The fallback heuristic's own gluten/wheat signal is captured
    # elsewhere (is_gluten_free), proving this product is NOT
    # allergen-free -- yet allergens_detected still reports "None".
    assert data.is_gluten_free is False
