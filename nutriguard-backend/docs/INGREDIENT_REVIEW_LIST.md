# Manual EN/BG ingredient review list

## Delivery and dependency

Backend-only extension of **unmerged** candidate-queue branch
`feat/backend-ingredient-candidate-queue-issue-23` at `d372985`.
This is not on main or deployed. No live database was read or changed.
The collection hook and table come from that branch, not a second queue.
See `INGREDIENT_CANDIDATE_QUEUE.md` for scan coverage and migration tests.

## Owner commands (after reviewed integration and deployment)

From the backend directory with the usual server environment:

```sh
python -m app.seed.ingredient_review_list
python -m app.seed.ingredient_review_list --output /home/vboxuser/nutriguard-review/ingredients.json
```

Create the dedicated output directory beforehand. With Docker, use
`docker compose exec -T backend python -m app.seed.ingredient_review_list`
to read the list. An `--output` path inside a container is a container path;
use an explicitly configured private mounted directory if a host file is needed.
Do not assume an arbitrary host path is mounted. Never use public web storage.

The first command prints JSON; the second atomically replaces one UTF-8 JSON
snapshot. Open it with any text editor. It is a snapshot, not a live file: run
the command again after testing products. No scheduler, public endpoint, API
translation call, image archive, or growing text log is added.
Failure exits nonzero and preserves the previous snapshot. No successful empty
report is fabricated when the database or schema is unavailable.

## What is collected and shown

- The existing queue collects **new/unreviewed identities**, not already-known
  curated/regulatory ingredients; repeat scans update observations.
- The owner view groups all observations pointing to one canonical ingredient
  ID into one row. EN/BG aliases for that identity do not create extra rows.
- `nameEn` and `nameBg` use explicitly language-tagged aliases, curated canonical
  English, or current hash-matching REVIEWED Bulgarian localization. Missing
  translations are null, never invented. Machine-translated aliases may appear
  as identity text but are not reviewed scientific information.
- Untagged OCR text is not assumed English or Bulgarian from its alphabet or
  a few stopwords. Its ID still appears, with `NAME_LANGUAGE_REVIEW_REQUIRED`.
  Some genuinely English/Bulgarian legacy names will therefore need manual
  language assignment. This deliberately prioritizes avoiding foreign text over
  displaying every unverified name. Raw observations remain in the internal
  queue for investigation; they are **not** copied into this EN/BG owner list.
- Known junk is excluded and counted. Missing/deleted identity links appear by
  observation ID, without exporting their original unknown-language name.
- Different IDs with the same name are flagged, not merged. Absolute semantic
  deduplication is impossible until those identities are reviewed. E-number
  matches and aliases are resolved by the existing catalog, not by this exporter.
- Fields include IDs, E-number, first/last encounter, flags and
  `tokenObservationCount`. That count sums token observations; it is NOT unique
  users or unique scans across aliases.
- Existing queue limits apply: default 5,000 token rows, soft under concurrency;
  new tokens at capacity are refused and logged. No claim of unlimited or
  guaranteed collection. No automatic eviction or manual prune is run here.

Old catalog rows are not automatically backfilled. Existing maintenance offers
`python -m app.seed.ingredient_candidate_maintenance backfill` (dry run).
Review its report before separately authorizing `backfill --apply`.

## Manual enrichment workflow

1. Read the owner list and choose `ingredientId` (not name alone).
2. Resolve flagged identity/name collisions and language assignments with actual
   evidence. Never merge source-specific compounds solely by an E-code or name.
3. Prepare EN/BG descriptions and source URLs in a separate reviewed content
   change, retaining field-level provenance and localization source hashes.
4. Validate with tests/review before importing through the project's content
   tooling. Do not directly set all verification flags or edit production SQL
   merely because a description has been written.

Editing this JSON **does not change the app**. It is a review/export view, not an
import format or content editor. A general-purpose manual-content importer is
not implemented by this change. Existing summary/content branches must be
integrated and reviewed separately; no automatic evidence promotion is added.

## Verification and integration gates

New tests cover identity grouping, language filtering, stale/draft BG exclusion,
name collisions, junk/orphans, atomic UTF-8 replacement/failure preservation, and
real scan-materialization-to-report with database state unchanged.

This change adds no migration/API changes. Before integration, reconcile the
candidate migration revision `d7e8f9a0b1c2` with other unmerged branches using
that revision ID, then test the final combined chain against disposable Postgres.
Do not merge all parallel branches blindly or deploy this branch over unrelated
Android/backend work. Linux and disposable PostgreSQL verification of commit
`c3f2f4e` is complete; see `INGREDIENT_REVIEW_VERIFICATION.md` for exact results,
the added PostgreSQL CLI regression, and unresolved cross-branch integration gates.
Any combined integration tree still needs its own full verification.
