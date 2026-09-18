"""
Issue #21 audit reproduction (section 1: unknown-value/API semantics),
committed as an INTENTIONAL reproduction of a confirmed problem, not a
regression test for a fix. See
nutriguard-backend/docs/BACKEND_DATA_QUALITY_AUDIT.md ("Finding 2").
Does not modify or weaken any existing test.

README section 6 item 11 documents that `FullProductAnalysisOut.healthScore`
(the `/scan/*` response) was deliberately made `int | None` so a client
never mistakes "not computed" for "a genuine score of 0" -- and item 6
confirms `POST /scan/barcode` never includes `healthScore` at all in a
`labelScanRequired` (incomplete) response body.

`ProductOut.health_score` (used by `GET /products/{barcode}` and
`GET /products`, `app/schemas/product.py`) was NOT given the same
treatment -- it is a plain, non-nullable `int`. A materially incomplete
barcode-discovery result is still persisted (by design -- see
`food_analysis._persist_discovered_product`'s own docstring: "Always
persists identity/provenance ... including a materially-incomplete,
`is_verified=False` result") with `health_score=0` as an explicit
placeholder (`food_analysis.py`, `_to_product_model`: `health_score=0,
# placeholder, recomputed live on every read`). `GET /products/{barcode}`
is a "plain lookup, no warning/score recomputation" (its own docstring)
that returns this stored value as-is.

Net effect demonstrated below: a client reading `GET /products/{barcode}`
for a real, never-verified, nutrition-incomplete discovery sees
`healthScore: 0` -- textually and numerically identical to what a
genuinely verified product with the worst possible real Health Score
would show on the exact same field. `isVerified`/`hasVerifiedNutrition`
are present on the same object and DO let a careful client
disambiguate, but nothing in the OpenAPI schema or docs states that
`healthScore` on `ProductOut` must always be read jointly with
`isVerified` -- unlike the scan-response path, which closes this exact
ambiguity by using `null`.
"""
import pytest

from app.core.config import settings
from app.integrations.barcode_providers.base import NutritionFacts, ProviderProductResult
from app.services import barcode_discovery

from tests.integration.test_barcode_discovery_flow import FakeProvider, _patch_providers, _register_device

BARCODE = "9501101530003"  # valid, checksum-passing EAN-13, unused elsewhere in this suite


@pytest.mark.asyncio
async def test_unverified_discovered_product_reports_health_score_zero_on_plain_lookup(app_client, monkeypatch):
    off = FakeProvider(
        "open_food_facts",
        0.75,
        ProviderProductResult(
            provider="open_food_facts",
            external_id="audit-issue21-1",
            product_name="Audit Partial Nutrition Item",
            brand="Audit Brand",
            category="Test",
            image_url=None,
            raw_ingredient_text="Water, Sugar, Citric Acid",
            # Only one of the three core nutrition fields present -> nutrition_known=False
            # (see README section 6 item 9) -> is_verified stays False, but the
            # row is still persisted (see this file's module docstring).
            nutrition=NutritionFacts(sugar_grams=12.0, sodium_mg=None, saturated_fat_grams=None),
            allergens=[],
            dietary_flags={},
        ),
    )
    _patch_providers(monkeypatch, off=off)

    headers = await _register_device(app_client, "audit-issue21-zero-score")
    scan_resp = await app_client.post("/api/v1/scan/barcode", json={"barcode": BARCODE}, headers=headers)
    assert scan_resp.status_code == 404  # correctly refuses a fabricated score (unchanged behavior)
    assert "healthScore" not in scan_resp.json()  # the scan response correctly has no ambiguous field

    lookup_resp = await app_client.get(f"/api/v1/products/{BARCODE}", headers=headers)
    assert lookup_resp.status_code == 200
    product = lookup_resp.json()["product"]

    # This IS the gap: the plain-lookup endpoint's healthScore is
    # indistinguishable, on its own, from a real worst-possible score.
    assert product["healthScore"] == 0
    assert product["isVerified"] is False
    assert product["hasVerifiedNutrition"] is False
