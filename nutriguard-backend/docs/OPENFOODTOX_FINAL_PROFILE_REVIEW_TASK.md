# Claude: targeted profile corrections, not a new redesign

Date: 2026-10-01. Baseline: b915ccdabc23729110d41e32df15876adb7863ad.
Read applicable instructions and the profile presentation specification. Continue in
an isolated worktree on feat/backend-openfoodtox-dataset-audit. Commit/push scoped
code, tests and docs without force-push. Do not change live data/checkout/containers,
app/API/seed/Health Score, scientific review status, or merge/deploy.

## 1. One policy for ALL numeric intake claims

The generated E150d preview still shows 300 and 100 mg/kg/day in editorial effects
while the extracted numeric claim is withheld, then says the number is not shown.
The existing test only protects structured reference-value rendering, not editorial
text. External sourcing is useful but does not itself exempt a claim from gating.

Represent editorial numeric intake claims explicitly with source, units, chemical
basis, group scope, population/exceptions and independent review/eligibility metadata.
Apply a single preview policy across effects, intake, notes and both languages.
For this round, keep unresolved/unreviewed E150d numeric claims in INTERNAL evidence
only; its consumer preview may explain the group scope without the numeric limits.
Do not suppress unrelated numbers such as E-codes, years or chemical identifiers.
Remove contradictory or technical eligibility/debug notes from consumer text; retain
all reasons in the operator bundle. Do not promote a claim to reviewed just to render it.
Preserve E951's PKU qualification and existing source-backed distinctions.

Test the entire rendered consumer preview for E150d, including editorial effects and
notes, in EN and BG; verify withheld quantities remain available internally. Test
that changing heading/source-kind cannot bypass the same rule, and that supported
preview claims keep group/population qualifiers. Keep DRAFT preview eligibility
distinct from production publication authorization.

## 2. Localized, non-technical titles

Use explicit EN/BG display names for the four pilot identities:
- Sodium nitrite / Натриев нитрит
- Sulphite ammonia caramel / Сулфитно-амонячен карамел
- Citric acid / Лимонена киселина
- Aspartame / Аспартам

Render the E-code once. E150d's ad-hoc-query status belongs in operator metadata,
not the ingredient heading. Never invent a live catalogue row for this display fix.
Add title tests for both languages and no technical-placeholder leakage. Fix obvious
BG grammar such as "каква точно количество" and simplify unnecessary jargon without
changing scientific meaning. Keep original names and source titles in provenance.

## 3. Traceable primary sources for editorial claims

Audit each editorial claim. A pointer to our seed JSON/CSV is an internal content
origin, not independent scientific evidence. Replace vague references such as
"standard food-chemistry references" with an actual primary source and exact relevant
section, or leave the claim unconfirmed/internal and omit it from consumer preview.
Do not cite a regulation for a manufacturing/effect statement it does not support.
Read relevant source passages, not search snippets. Keep external evidence separate
from OpenFoodTox extraction and preserve uncertainty and population limitations.

Commit a concise per-claim matrix (stable claim ID, EN/BG text or reference, exact
source URL/DOI, section, access date, supporting passage/paraphrase basis and review
status). The matrix must be reviewable via Git, not solely in an uncommitted VM JSON.
Maintain truthful not_reviewed statuses; AI source checking is not independent
scientific approval. Tests can check citation coverage and structure, not establish
the truth of a claim merely by matching fixture strings.

## Delivery and acceptance

Run focused tests and full pinned-dependency suite. Reconcile the reported count:
the user summary says 858 passed whereas CODEX_HANDOFF at b915ccd says 853 passed;
report the actual command/output and correct stale documentation, without guessing.
Regenerate only the pilot previews if extraction is unchanged; keep old output versions.
Commit complete updated EN/BG drafts, claim/source matrix, pilot report and handoff.
Return full pushed SHA and remaining blockers. No new architecture/features, full
dataset rerun unless necessary, automatic review promotion, merge or deploy.
