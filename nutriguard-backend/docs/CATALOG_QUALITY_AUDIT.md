# Catalog quality audit — manual, read-only first

## Scope and status

Added on top of ingredient summaries commit `255dde27b59785706eb714404cf57d0a1f80a000`.
This is the first executable slice of stage 4, **not completion of the full catalog programme**.
It does not modify the API, scientific ratings, translations, catalog, or scan path.
No live database has been queried, no scheduler enabled, and no repair applied.

## Files and safety

- `scripts/audit/catalog_quality.sql`: PostgreSQL aggregate snapshot. A repeatable-read,
  read-only transaction with a 15-second statement timeout and 2-second lock timeout;
  explicit rollback. No raw label text, names, barcodes, users, or device identifiers in output.
- `scripts/audit/catalog_quality_report.py`: dependency-free validator and comparison.
  Input limited to 16 KiB, exact fields, bounded nonnegative integer counters. No network
  or writes. Errors do not echo input or connection details.
- `tests/unit/test_catalog_quality_report.py`: eight standalone tests.

Run SQL with `ON_ERROR_STOP`: a schema mismatch or timeout is a failed audit, not a
healthy empty catalog. Do not substitute zeros after an error. The SQL requires the
existing identity uncertainty/localization columns from revision `c6d7e8f9a0b1`.

## Manual verification on a disposable PostgreSQL database FIRST

From the backend directory, with a **preconfigured** PostgreSQL service named
`nutriguard_audit_test` pointing at a disposable database:

```sh
psql 'service=nutriguard_audit_test' -X -qAt -v ON_ERROR_STOP=1 \
  -f scripts/audit/catalog_quality.sql > catalog-quality-new.snapshot.json
python scripts/audit/catalog_quality_report.py < catalog-quality-new.snapshot.json
```

Do not continue if the first command exits nonzero. Keep credentials in the normal
PostgreSQL credential mechanism, not commands, reports, or Git. Generated snapshots
belong in a dedicated operational directory outside the checkout.

For comparisons, pass an earlier **raw snapshot**, not the formatted report:

```sh
python scripts/audit/catalog_quality_report.py \
  --previous catalog-quality-previous.snapshot.json < catalog-quality-new.snapshot.json
```

The operator must confirm both snapshots belong to the same database. Timestamp
ordering is checked; database identity is deliberately not exported. Delta means
counter change, not a list of newly discovered ingredients or proof of improvement.

## Meaning of the counters

Coverage: ingredients/products/aliases, unverified ingredients, uncertain identities,
missing descriptions, E-number descriptions, and reviewed Bulgarian rows.
Quality signals: empty/no-letter names, leading ingredient headers, exact normalized
name collisions, malformed E-number notation, missing referenced IDs and affected
product count, and unreferenced catalog rows.

Important limitations:

- Equal names are review candidates, never automatic merge authorization.
- Unreferenced rows can be legitimate reusable knowledge, not garbage.
- Missing product references may be legacy read-time synthetic reconstruction.
- E-number check is syntax only, not authorization, validity, or a complete registry.
- Description presence does not mean reliable science; generic prose can still pass.
- Reviewed BG count is not a freshness or independent-human-review guarantee.
- Summary tables, candidate queue, translation hashes, per-field provenance, regulatory
  accuracy, and source freshness are **not checked yet**, explicitly listed in reports.
- Counts are not a numerical safety/quality score. No ingredient risk is changed.

## Bounded storage and scheduling

Manual operation remains the default. The reporter writes only stdout and cannot
accumulate files itself. Keep at most two validated snapshots (current/previous) and
one formatted report, each capped at 16 KiB by the eventual runner; never rotate a
failed or invalid snapshot over the last good one. Do not retain per-run raw logs.

A weekly scheduler is intentionally **not installed**. Before enabling one, implement
and test an operational runner with: exclusive lock, atomic replacement, fixed output
filenames, size limits, timeouts, separate failure status, and same-database identity
checks. Then configure a weekly timer in the deployment environment. This document
does not claim retention or scheduling has already been implemented.

## Verification and remaining integration gates

Local bundled Python: `python tests/unit/test_catalog_quality_report.py` → 8 passed.
`git diff --check` passed. No SQLAlchemy, Docker, or PostgreSQL runtime was available
in this shell, so the SQL has **not** been executed; full backend suite not rerun.
SQL guardrail tests are static, not a substitute for a real PostgreSQL test.

Before merge/deployment:

1. Exercise SQL against disposable PostgreSQL with representative duplicate names,
   empty headers, missing references, E-number variants, and placeholder descriptions.
   Verify counts, timeout handling, and before/after equality of all seeded data.
2. Integrate candidate queue and summaries branches explicitly. Candidate migration
   `d7e8f9a0b1c2` and summary migration `e8f9a0b1c2d3` both descend from
   `c6d7e8f9a0b1`; previous truthful-unknown work also used `d7e8f9a0b1c2`.
   Verify actual branch files/deployment state and resolve revision identity collisions
   before a merge migration. Do not rewrite an already-applied revision casually.
3. Add optional-table coverage and hash freshness using production hash functions;
   absent schema must report unavailable rather than zero.
4. Independently review pilot scientific content and Bulgarian drafts. Never mark
   machine translations human-reviewed automatically.
5. Finish bounded runner, test concurrency/failure recovery, then explicitly activate
   the schedule. No live repair, deduplication, or cleanup is authorized by audit output.

No push, PR, merge, deployment, live repair, or schedule activation performed here.
