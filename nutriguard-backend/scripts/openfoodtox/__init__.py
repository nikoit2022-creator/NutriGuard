"""Offline tooling to inventory, audit, and catalogue the transferred
OpenFoodTox / IUCLID dossier archives.

Everything in this package is read-only with respect to the source
archives and does not import, touch, or depend on the live NutriGuard
application (``app/``), its database, or its API. See
``nutriguard-backend/docs/OPENFOODTOX_DATASET_AUDIT.md`` for usage,
output schemas, and known limitations.
"""
