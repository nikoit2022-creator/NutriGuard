# Integrate the four OpenFoodTox profiles for application testing

Owner authorization, 2026-10-01: finish this update for testing. The owner
accepts source-backed content for display without an additional outside
expert review. This is publication permission for this bounded pilot, NOT
a claim that an independent scientist or human translator reviewed it.
Keep those provenance facts honest; do not globally weaken evidence gates.

Baseline: `037b3ccbeb91b91e539aa24474674e6b9687dc1e` on
`feat/backend-openfoodtox-dataset-audit`; main inspected at `65a8e68`.
Claude owns backend implementation; Codex owns Android integration/review.
Read repository instructions/latest handoff and check remote drift. Work in
an isolated checkout. Subagents allowed. No live operations in this task.

## Deliver working integration, not another offline report

1. Build a small tracked, versioned, structured EN/BG content artifact for
   E250, E150d, E330 and E951 using the supported pilot content. Do not parse
   rendered Markdown into runtime fields or require the bulk VM dataset at
   runtime. Preserve claim/source attribution, content version, identity and
   owner publication permission separately from scientific review status.
2. Implement an explicit, transactional, idempotent dry-run/apply import
   path with an exact four-identity allowlist. Resolve official identifiers
   without collapsing suffixes or stealing ambiguous aliases. Provision
   missing E150d as a distinct ingredient; preserve existing product links.
   Report proposed changes before apply. Do not invoke the existing broad
   seed merge indiscriminately: it can overwrite unrelated scientific fields.
3. Update only approved display fields and their honest provenance. Preserve
   risk_level, risk_assessment_available, regulatory approvals, dietary flags
   and scoring inputs. No automatic scientific verification promotion. Prove
   the pilot leaves existing product Health Scores unchanged. Avoid a
   blanket VERIFIED assignment just to expose text or numbers.
4. Serve EN/BG content consistently through direct ingredient, product,
   barcode, OCR-text, label-image and partial-result serialization paths.
   Existing localizations require REVIEWED + current hash: implement a
   narrowly scoped, explicit owner-approved-publication mechanism if needed,
   without falsely marking machine content human-reviewed or allowing every
   draft through. Preserve current behavior for all non-pilot ingredients.

## Field contract for Codex (publish before Android implementation)

Prefer existing display fields where truthful. Commit an exact mapping and
representative response fixture for each language and all four identities:

| Content | Intended UI destination |
| --- | --- |
| Localized ingredient name and E-code | Heading; E-code distinct and once |
| What it is / origin | Description |
| Technological role | Purpose in food |
| Human-relevant effects and evidence limitations | Health considerations |
| Conditions, susceptible populations, PKU | Adjacent to relevant effect/intake |
| Supported intake guidance with basis/scope/units | Intake section, never serving-size advice |
| DOI/URLs | Sources at bottom |

The current Android main does NOT consume effectConditions/dietaryGuidance/
adiPopulationScope. Its numeric ADI display takes precedence over text, so
bare numbers may hide caveats. Do not rely on unseen fields to communicate
essential qualifications. Document fields Codex must wire end-to-end (DTO,
Room/entity, localization, view) and any required migration. Do not edit
Android yourself. Keep E150d withheld numeric values withheld unless separately
validated against the same eligibility policy; owner publication permission
alone does not resolve a chemical-basis ambiguity. Do not infer limits from
missing data. No draft labels, internal quotes, technical gating explanations
or operator-only notes in consumer display text. Omit absent sections.

## Tests and handoff

- Test idempotent import, wrong identity/suffix rejection, rollback, preservation
  of references/scoring/approvals and unrelated records; use disposable DBs.
- Test each API path and EN/BG mapping with all four fixtures, missing content,
  stale translations and ordinary non-pilot draft fallback. No false review flags.
- Run full backend suite, disposable Postgres tests and any migration cycle;
  regenerate/compare pinned OpenAPI for contract changes.
- Document exact dry-run/apply/verification commands and rollback approach;
  a future deployment must backup first and avoid bind-mount hot reload
  against mismatched schema. Do not execute deployment now.
- Update CODEX_HANDOFF and add a focused integration report/contract. Commit
  and push normally to the same branch. Return full SHA and exact test results.

Stop after tested code and contract are pushed. No merge/deploy, live DB,
container restart, secrets changes, external calls per scan or bulk imports.
Codex then wires/verifies Android against the published fixtures before the
combined release proceeds to device testing.
