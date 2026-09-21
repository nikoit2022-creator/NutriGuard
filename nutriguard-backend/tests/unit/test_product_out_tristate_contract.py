"""
Wire-contract pin for the issue #21 follow-up (README section 6 item 17):
`ProductOut`'s six dietary flags and `healthScore` are nullable but their
keys are ALWAYS present; `allergensDetected` stays a plain non-null string;
the Health Score availability gate is `isVerified`, not the number.
"""
import json
from types import SimpleNamespace

import pytest

from app.main import app
from app.schemas.product import ProductOut

FLAGS = ("isGlutenFree", "isLactoseFree", "isVegan", "isVegetarian", "isHalal", "isKosher")


def _product(**overrides) -> dict:
    fields = dict(
        barcode="1", product_name="P", brand="B", category="C", raw_ingredient_text="", ingredient_ids="",
        health_score=55, nova_group=3, sugar_grams=1.0, sodium_mg=1.0, saturated_fat_grams=1.0,
        has_artificial_sweeteners=False, has_preservatives=False,
        is_gluten_free=None, is_lactose_free=None, is_vegan=None, is_vegetarian=None, is_halal=None, is_kosher=None,
        allergens_detected="", timestamp=0,
        has_verified_nutrition=True, has_verified_ingredients=True, is_verified=True,
    )
    fields.update(overrides)
    return fields


def test_openapi_marks_only_the_intended_product_fields_nullable_and_keeps_them_required():
    schema = app.openapi()["components"]["schemas"]["ProductOut"]
    for name in (*FLAGS, "healthScore"):
        assert name in schema["required"], name  # key always present
        assert {"type": "null"} in schema["properties"][name]["anyOf"], name  # value may be null
    assert schema["properties"]["allergensDetected"]["type"] == "string"  # unchanged, non-null
    assert "allergensDetected" in schema["required"]
    assert {"type": "null"} not in schema["properties"]["isVerified"].get("anyOf", [])


def test_all_keys_are_serialized_even_when_null():
    out = ProductOut.model_validate(SimpleNamespace(**_product()))
    payload = json.loads(out.model_dump_json(by_alias=True))
    for name in FLAGS:
        assert name in payload, name  # the key is present...
        assert payload[name] is None, name  # ...with an explicit null (never omitted, never false)
    assert payload["healthScore"] == 55  # verified -> the real score
    assert payload["allergensDetected"] == ""


@pytest.mark.parametrize(
    "verified, stored, expected",
    [
        (True, 0, 0),  # genuine computed worst score is NOT collapsed
        (True, 63, 63),
        (True, None, None),
        (False, 0, None),  # legacy placeholder never surfaces
        (False, 63, None),  # nor does any stale value on an unverified product
        (False, None, None),
    ],
)
def test_health_score_availability_is_decided_by_is_verified_not_by_the_number(verified, stored, expected):
    out = ProductOut.model_validate(SimpleNamespace(**_product(is_verified=verified, health_score=stored)))
    assert out.health_score == expected


def test_explicit_tri_state_values_round_trip():
    out = ProductOut.model_validate(
        SimpleNamespace(**_product(is_vegan=True, is_halal=False, is_kosher=None))
    )
    assert (out.is_vegan, out.is_halal, out.is_kosher) == (True, False, None)
