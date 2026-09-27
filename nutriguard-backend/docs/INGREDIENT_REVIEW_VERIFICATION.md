# Ingredient review list: Linux verification, 2026-09-27

Verified target: `c3f2f4eefa8c7ef839c95945496471ee2871fbe9` on
`feat/backend-ingredient-review-list`. The fetched branch matched exactly.
Worktree: `/home/vboxuser/nutriguard-worktrees/feat-backend-ingredient-review-list`.

Result: the requested commit passes the Linux backend suite and disposable
PostgreSQL checks. No production-code fix was needed. Added one PostgreSQL
regression test for the actual owner export command. This verifies this branch;
it does not approve a combination with other unmerged branches or deployment.

## Scope and preserved behavior

The target commit adds only a manual export, documentation and tests on top of
candidate queue commit `d37298597033b30edd5fe9639873d8498f7e7c3e`.
The exporter reads the existing queue, groups by canonical ingredient ID, and
never merges distinct identities merely because names look alike. EN/BG names
come from language-tagged aliases, trusted canonical English or current reviewed
Bulgarian localization. Untagged OCR names remain null and flagged for language
review; no alphabet-based language inference or new translation is added.
Known curated identities are not newly queued. Existing observations can remain
after curation. This is a review list of observed unreviewed identities, not an
inventory of every ingredient in every scan.

No descriptions, scientific evidence, information sources, verification flags,
analysis API fields, EFSA integration or automatic jobs were added or changed
by this verification or the export commit. Existing analysis and translation
services remain the source of identities. Export counts are token observations,
not unique scans across aliases. Queue capacity is still a soft 5,000-row default.

The inherited candidate commit DOES change behavior relative to main: it fixes
fragment-based identity matching, excludes junk identities, removes provider
failure placeholders and preserves saved ingredients after untrusted extraction.
Those documented dependency changes must be included in review; this branch is
not an export-only diff against main. Existing scoring and scientific content
remain unchanged. Full regression tests exercise that inherited behavior.

## Integration review

Fetched `origin/main` and the live checkout are at
`32bd7efc05750470da6f48eab7c4111e45db1d26`. Read-only PostgreSQL inspection
(`BEGIN READ ONLY; SELECT version_num FROM alembic_version;
SELECT to_regclass('ingredient_candidates'); COMMIT;`) returned
`c6d7e8f9a0b1` and no candidate table. Docker mount inspection confirms the live
backend binds `app` and `alembic` from `/home/vboxuser/nutrigard/nutriguard-backend`.
Its pre-existing uncommitted handoff edit was left untouched. No live application
or migration files were edited, no live data was exported or changed, and no
service was restarted. Collection remains inactive on the live stack.

| Branch / fetched tip | Dependency and conflict |
| --- | --- |
| Candidate queue / `d372985` | Required and already an ancestor. Adds `d7e8f9a0b1c2` after `c6d7e8f9a0b1`; no other unmerged dependency. |
| Truthful unknowns / `86f94f6` | Optional, not included. Its **different** `d7e8f9a0b1c2_tristate_product_flags_nullable_score.py` uses the same revision ID. A textual merge cannot detect this schema ambiguity. `git merge-tree` also reports handoff and migration-test conflicts. |
| Ingredient-first label scan / `76d92f2` | Optional, not included. `git merge-tree` reports a conflict in `app/services/food_analysis.py`. Preserve both `ingredients_trustworthy` and `not resolution_failed` guards when integrating, plus empty provider-failure ingredients and the newer diagnostics/return shape. Re-run scan regressions on that eventual combination. |
| Ingredient summaries / `255dde2` | Optional and outside names-only scope. Adds distinct `e8f9a0b1c2d3`, also parented to `c6d7e8f9a0b1`; combining creates two heads until explicitly reconciled. Textual conflicts in README and model registration. |
| Catalog quality audit / `efad10b` | Contains the summaries migration/content stack as well as audit tooling; not a required read-only dependency. Do not integrate the whole branch for this export. |
| E-additive summaries / `ee1111a` | Optional content branch, not required or integrated; no new migration at this fetched tip. |

Migration decision: keep this branch's existing queue revision unchanged while
verifying it alone. Before combining with truthful unknowns, assign one of the
two different migrations a unique revision and explicitly reconcile ancestry
and migration assertions. If either revision has been applied elsewhere, inspect
the actual schema and plan a forward migration; never assume an Alembic version
string identifies the correct table changes and never blindly stamp or rename
an applied revision. For an eventual summaries combination, choose a reviewed
linear order or a proper merge revision, then verify the entire chain again.
No branch merge, rebase, or deployment was performed; `git merge-tree --write-tree`
was used only to inspect synthetic merge results without changing the index.

## Executed verification

Environment: Linux, Python **3.12.14**, existing `ng-stage3-test:dev` dependency
image with this worktree mounted read-only over `/app`. Every pinned package in
this branch's `requirements.txt` was checked with `importlib.metadata` and
matched exactly. No existing image application code was used. PostgreSQL was
`postgres:16-alpine` on a dedicated internal Docker network, without published
ports, with disposable tmpfs data. Application entrypoints were overridden;
Redis, Gemini and barcode discovery were disabled in the PostgreSQL runner.

| Check | Result |
| --- | --- |
| Target full suite: `python -m pytest -q -p no:cacheprovider` in a network-disabled container | **701 passed, 15 skipped, 2 warnings**, 19.53 seconds. All skips were opt-in PostgreSQL tests. The three prior Windows-only failures pass on Linux. |
| `python -m alembic heads` | Single head `d7e8f9a0b1c2`. |
| `python -m pytest tests/postgres -q -p no:cacheprovider`, with disposable `NUTRIGUARD_TEST_POSTGRES_URL` | **16 passed**, zero skips/failures, 3.43 seconds: 15 existing checks plus the new export regression. |
| Final full suite: `python -m pytest -q -p no:cacheprovider`, with disposable PostgreSQL enabled | **717 passed, zero skipped, 2 warnings**, 22.05 seconds, including the new regression. |
| `app.openapi() == json.loads(Path('openapi.json').read_text())` | Exact match. |
| `python -m alembic upgrade c6d7e8f9a0b1`, fixtures, `upgrade head`, `downgrade c6d7e8f9a0b1`, `upgrade head` | All passed; existing rows identical after every transition. |
| `python -m app.seed.ingredient_candidate_maintenance backfill` and `prune` | Dry runs preserved database state; backfill reported one potential row, zero created; prune zero matched/removed. |

Migration fixtures used the existing seed loader (47 identities and their aliases
and reviewed localizations), one additional OCR identity/alias, and one product
referencing curated and OCR identities. The final fixture resolves the curated
ID from the database instead of assuming a seed ID. Compared complete sorted row snapshots
of ingredients, aliases, localizations and products across all three transitions.
Verified six candidate indexes including primary/unique indexes after upgrade
and absence of the table after downgrade. Reset only the disposable schema and
migrated afresh before running the PostgreSQL test suite.

The existing PostgreSQL suite covers concurrent new/existing observations,
opposite-order lock acquisition, identity deletion with observation retention,
status/count constraints, catalog alias/identifier convergence and concurrent
product enrichment. New file
`tests/postgres/test_ingredient_review_list_postgres.py` verifies:

- Real materialization and EN/BG observations export as one identity with both
  names, two candidate IDs and the correct summed count.
- The real export session uses PostgreSQL READ ONLY, REPEATABLE READ, a 15-second
  statement timeout and a 2-second lock timeout.
- The actual CLI writes matching UTF-8 JSON; an unavailable database exits 1,
  prints a generic error and preserves the previous snapshot.
- Complete ingredient, alias, localization, candidate and product row snapshots
  remain identical after exports and failure handling.

The original 15 export tests additionally cover stale/draft BG localization
exclusion, name collisions, language uncertainty, junk/orphans and atomic replace
failure. Existing SQLAlchemy duplicate-identity warnings and the pytest-asyncio
fixture-loop deprecation warning remain unchanged.

Disposable containers and their private network were removed after verification.
`git diff --check` passed. No production-code or migration change was necessary.

## Remaining gates and next step

Review this branch together with its candidate dependency and this verification
test/report. Any later combination with diagnostics, label-scan or content work
requires explicit conflict resolution and fresh tests on the resulting tree.
After a separately approved integration/deployment, review a dry-run backfill
before deciding whether to collect legacy identities. No backfill, migration,
merge, deployment or scheduler activation is authorized by this report.
