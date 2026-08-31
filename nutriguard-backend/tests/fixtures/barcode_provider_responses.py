"""
Realistic (hand-authored, not scraped) fixture JSON bodies shaped like
each barcode provider's real, documented API response format. No bulk
or copyrighted product data — just enough structure to exercise each
adapter's parsing logic.
"""

OFF_FOUND_FULL = {
    "code": "4006381333931",
    "status": "success",
    "result": {"id": "product_found"},
    "product": {
        "product_name_en": "Fizzy Orange Soda",
        "product_name": "Fizzy Orange Soda",
        "brands": "Sunburst, Acme Beverages",
        "categories": "Sodas, Beverages",
        "image_front_url": "https://images.example-off.org/4006381333931/front.jpg",
        "ingredients_text": "Carbonated Water, Sugar, Citric Acid, Sodium Benzoate, Orange Flavoring",
        "ingredients": [
            {"id": "en:water", "text": "Carbonated Water"},
            {"id": "en:sugar", "text": "Sugar"},
        ],
        "nutriments": {
            "sugars_100g": 10.5,
            "sodium_100g": 0.04,
            "saturated-fat_100g": 0.0,
        },
        "nova_group": 4,
        "allergens_tags": [],
        "traces_tags": [],
        "ingredients_analysis_tags": ["en:vegan", "en:vegetarian"],
        "labels_tags": ["en:gluten-free"],
        "additives_tags": ["en:e211"],
        "lang": "en",
        "lc": "en",
        "last_modified_t": 1735689600,
    },
}

OFF_FRENCH_ONLY = {
    "code": "3017620422003",
    "status": "success",
    "result": {"id": "product_found"},
    "product": {
        # No product_name_en / product_name_bg at all -- only the
        # language-neutral default field, in French, with lang/lc
        # correctly declaring it as such.
        "product_name": "Pâte à Tartiner aux Noisettes",
        "ingredients_text": "Sucre, huile de palme, noisettes, cacao maigre",
        "brands": "Nutchoc",
        "nutriments": {"sugars_100g": 56.0, "sodium_100g": 0.03, "saturated-fat_100g": 10.6},
        "lang": "fr",
        "lc": "fr",
    },
}

OFF_EXPLICIT_ENGLISH_FIELD_ON_NON_ENGLISH_RECORD = {
    "code": "3017620422010",
    "status": "success",
    "result": {"id": "product_found"},
    "product": {
        # Record's own declared language is German, but it also
        # supplies an explicit English translation -- that explicit
        # field must always be accepted regardless of `lang`.
        "product_name": "Nuss-Nougat-Creme",
        "product_name_en": "Hazelnut Spread",
        "ingredients_text": "Zucker, Palmoel, Haselnuesse, Kakao",
        "ingredients_text_en": "Sugar, palm oil, hazelnuts, cocoa",
        "brands": "Nutchoc",
        "nutriments": {"sugars_100g": 56.0, "sodium_100g": 0.03, "saturated-fat_100g": 10.6},
        "lang": "de",
        "lc": "de",
    },
}

OFF_FRENCH_INGREDIENTS_WITH_ENGLISH_NAME = {
    "code": "3029330003533",
    "status": "success",
    "result": {"id": "product_found"},
    "product": {
        # Explicit English identity is present and usable...
        "product_name_en": "Hazelnut Spread",
        "brands": "Nutchoc",
        # ...but there's no ingredients_text_en/_bg, and the record's
        # own declared language is French -- ingredients_text (and the
        # `ingredients[].text` array) must NOT be used as the ingredient
        # source; only the array's always-English `.id` values may be.
        "ingredients_text": "Sucre, huile de palme, noisettes, cacao maigre",
        "ingredients": [
            {"id": "en:sugar", "text": "Sucre"},
            {"id": "en:palm-oil", "text": "huile de palme"},
        ],
        "nutriments": {"sugars_100g": 56.0, "sodium_100g": 0.03, "saturated-fat_100g": 10.6},
        "lang": "fr",
        "lc": "fr",
    },
}

OFF_BULGARIAN_RECORD = {
    "code": "3800020827980",
    "status": "success",
    "result": {"id": "product_found"},
    "product": {
        "product_name": "Кисело Мляко",
        "ingredients_text": "Прясно мляко, млечни закваски",
        "brands": "Danone",
        "nutriments": {"sugars_100g": 4.0, "sodium_100g": 0.05, "saturated-fat_100g": 3.0},
        "lang": "bg",
        "lc": "bg",
    },
}

OFF_NOT_FOUND = {
    "code": "0000000000000",
    "status": "failure",
    "result": {"id": "product_not_found", "name": "Product not found"},
}

OFF_MALFORMED = {"code": "4006381333931", "status": "success"}  # missing `product` entirely

UPCITEMDB_FOUND = {
    "code": "OK",
    "total": 1,
    "offset": 0,
    "items": [
        {
            "ean": "0036000291452",
            "upc": "036000291452",
            "title": "Acme Toasted Oats Cereal, 18oz",
            "brand": "Acme",
            "category": "Food, Beverages & Tobacco > Food Items > Cereal",
            "images": ["https://images.example-upcitemdb.com/036000291452.jpg"],
        }
    ],
}

UPCITEMDB_NOT_FOUND = {"code": "OK", "total": 0, "items": []}

UPCITEMDB_RATE_LIMITED = {"code": "OVER_QUOTA", "message": "You have reached your daily request limit."}

UPCITEMDB_MALFORMED = {"code": "OK", "total": 1, "items": [{"upc": "036000291452"}]}  # no title

GS1_LINKSET_FOUND = {
    "linkset": [
        {
            "anchor": "https://id.gs1.org/01/04006381333931",
            "gs1:pip": [
                {
                    "href": "https://brand.example.com/products/04006381333931",
                    "title": "Product page",
                    "type": "text/html",
                }
            ],
        }
    ]
}

GS1_LINKSET_EMPTY = {"linkset": []}
