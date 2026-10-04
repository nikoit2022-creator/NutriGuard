# Local product catalog snapshot

`local_products.json` contains all 99 product records exported from the local
NutriGuard `products` table on 2026-10-04. `exportedAt` records the exact export
time. Products are sorted by barcode, and barcode values remain strings.

This is a JSON catalog snapshot, not a database backup or an API response.
Product field names match the database columns (snake_case); the public API
contract is unchanged. Original product values, source and verification flags
are preserved. The catalog includes test records, incomplete scans, generic
product names and non-food discovery results present in the local table; it is
not a curated or independently verified catalog.

Only product records are included. Related ingredient catalog records and
product source evidence are not bundled. Ingredient IDs may reference the
separate ingredient catalog or synthetic identities. Users, scan histories,
health profiles, credentials and other database tables are not exported.

The application does not automatically load this file. Any future importer must
handle schema compatibility, ingredient references and existing barcodes before
applying it to a database.
