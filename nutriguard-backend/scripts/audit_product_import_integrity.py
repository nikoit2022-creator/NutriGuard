"""Read-only audit of a CSV export of the products table.

Usage: python -m scripts.audit_product_import_integrity --input products.csv
The script never connects to a database and never rewrites the input.
"""

import argparse
import csv
import html
import json

from app.services.product_label import dietary_statuses, split_ingredients_and_traces, stated_allergens


def audit(row: dict[str, str]) -> dict[str, object]:
    raw = row.get("raw_ingredient_text", "") or ""
    _, traces = split_ingredients_and_traces(raw)
    named = stated_allergens(raw)
    stored = {part.strip().casefold() for part in (row.get("allergens_detected") or "").split(",") if part.strip()}
    aliases = {"eggs": "eggs", "peanuts": "peanuts", "soybeans": "soybeans", "nuts": "nuts", "milk": "milk", "gluten": "gluten", "sesame": "sesame"}
    missing = sorted(a for a in named if aliases[a.casefold()] not in stored)
    ids = [x for x in (row.get("ingredient_ids") or "").split(",") if x]
    status_row = type("ProductRow", (), {
        "raw_ingredient_text": raw,
        **{key: row.get(key) == "t" for key in (
            "is_gluten_free", "is_lactose_free", "is_vegan", "is_vegetarian", "is_halal", "is_kosher"
        )},
    })()
    return {
        "barcode": row.get("barcode", ""),
        "product_name": row.get("product_name", ""),
        "html_entities_in_name": html.unescape(row.get("product_name", "")) != row.get("product_name", ""),
        "possible_traces": traces,
        "missing_named_allergens": missing,
        "stored_ingredient_ids": len(ids),
        "synthetic_ingredient_ids": sum(x.startswith("synth_") for x in ids),
        "dietary_statuses": dietary_statuses(status_row),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="products CSV exported from PostgreSQL")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8", newline="") as source:
        rows = [audit(row) for row in csv.DictReader(source)]
    print(json.dumps({"products": len(rows), "rows": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
