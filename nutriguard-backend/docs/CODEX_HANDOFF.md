# CODEX_HANDOFF

## Current work

Date: 2026-09-03

- Branch: `feat/backend-canonical-product-evidence`, based on GitHub
  `origin/main` at `9c0dc347d4eb09195c5c56a1157e966a9990fc07`.
- Backend-only changes; the separate Android branch/commit was not included.
- Barcode-linked label scans now refresh complete first-party ingredient or
  nutrition groups on the same canonical product row. Invalid/incomplete
  groups preserve earlier verified evidence. Existing-row enrichment uses a
  row lock where supported; new-row SAVEPOINT conflict handling is unchanged.
- Synthetic ingredient display names are reconstructed from stored raw label
  text, preventing visible `Synth_*`/hash names.
- Added explicit nutrition basis and serving metadata, migration
  `b2c3d4e5f6a7`, schema/OpenAPI fields, and basis-gated scoring.
- Added an opt-in, bounded JSONL label-scan diagnostic journal with a persistent
  Docker volume. It excludes images, OCR/model payloads, secrets, user IDs and
  health-profile data.

## Files involved

Core areas: `app/services/food_analysis.py`, `gemini_image_parser.py`,
`ocr_normalizer.py`, `app/models/product.py`, `app/schemas/product.py`,
`app/api/v1/scan.py`, `app/core/scan_diagnostics.py`, configuration/Compose,
`openapi.json`, migration `b2c3d4e5f6a7`, README, and focused unit/integration
tests.

## Verification

- `python -m pytest -q`: **326 passed, 1 skipped**, 1 existing SQLAlchemy
  identity-map warning.
- Focused unit tests: **71 passed**.
- Focused label/barcode integration tests: **103 passed**, 1 existing warning.
- Runtime OpenAPI comparison: `OPENAPI_MATCH True`.
- `python -m alembic heads`: one head, `b2c3d4e5f6a7`.
- `python -m alembic upgrade head --sql`: successful PostgreSQL offline SQL.

## Unresolved items

- The opt-in real-PostgreSQL concurrency test is the single skipped test and
  was not run locally because Docker/PostgreSQL is unavailable on this Windows
  host.
- Run a real PostgreSQL upgrade/downgrade/upgrade and concurrency check on the
  VM before deploying.
- Android must later render `nutritionBasis` correctly (per 100 g vs per
  100 ml) instead of hardcoding `per 100g`; the fields are additive, so the
  existing client remains wire-compatible meanwhile.

## Recommended next step

Review the backend diff, run the real PostgreSQL migration cycle on the VM,
then commit/push this branch and open a PR. Do not merge or deploy until review
and CI are green.
