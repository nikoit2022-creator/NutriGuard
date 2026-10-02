# Claude task: offline catalogue matching and review-profile pilot

Authorized 2026-10-01. Baseline: af195fd2a63b35ebd9496e0081f66f30893ed5bd.
Read repository instructions, the latest handoff, OPENFOODTOX_DATASET_AUDIT.md,
and OPENFOODTOX_PROFILE_PRESENTATION.md first. The previous review tasks are
historical; this is the next stage, not a request to repeat completed fixes.

## Scope and ownership

Claude: offline matcher, evidence bundles, tests and source/reuse audit.
Codex: presentation specification and review of returned EN/BG draft profiles.
Work in an isolated worktree on the existing OpenFoodTox branch; scoped commits
and non-force push are authorized. Do not merge, deploy, modify the live checkout,
query/write the live database, change API contracts, migrations, Android, seed data,
scientific verification status, localization review status or Health Score.
No scheduled jobs, live external-lookup integration, or automatic enrichment.

## Inputs and reproducibility

- Use /home/vboxuser/nutriguard-data/openfoodtox/staging/v3/ and original dossiers
  read-only. Preserve all previous outputs. Use a new versioned pilot output directory.
- Build a minimal catalogue snapshot from tracked seed data using an isolated test
  database if required. Support a separately supplied authorized ingredient-only
  JSON snapshot for later use; do not fetch a live export in this task.
- Record input hashes, extraction schema/version and Git SHA. Clearly label the
  report's coverage as seed/supplied-snapshot coverage, not production DB coverage.
- Stream/index the large JSONL instead of loading it all into memory. Outputs must
  be deterministic and repeatable, with no implicit network calls in the matcher.

## Matching rules

Implement dry-run matching only. Use exact, normalized, explicitly sourced E-number
and CAS identifiers. Require agreement when both are present; conflicting identifiers
must remain unresolved even if one matches. Missing identifiers are not evidence
of equivalence. No fuzzy names, AI identity guesses, or assignment by list position.
Preserve suffixes, roman qualifiers, ranges, feed-only qualifiers, salts, mixtures
and isomers. Ranges/ambiguous candidates cannot become exact single-substance links.
Report exact match, no match, ambiguous and conflicting identifiers with explanations
and provenance. Multiple assessments of one substance are not duplicate substances.
Do not treat mechanically derived identifiers as independently verified evidence.

Resolve document/subject links, not merely a dossier title or presence in the same
archive. Do not attach every endpoint from an archive to every identity in it.
Quarantine incomplete extraction, unresolved subject linkage and conflicting evidence.
Keep assessment dates separate from archive/export dates; do not assume newest file
means newest scientific opinion or that the dataset is current for all substances.

## Pilot profiles

Try E250, E150d, E330 and E951. Report absence or ambiguity honestly; no substitute
substance or invented description. For exact matches produce a source-linked evidence
bundle and proposed EN/BG review text following OPENFOODTOX_PROFILE_PRESENTATION.md.
Keep extracted facts, external verification, interpretation and translation separate.
Draft text stays DRAFT; never write REVIEWED/VERIFIED to application data.
Do not force descriptions of origin/function when the available evidence lacks them.

Reference values must retain type, chemical basis, units, qualifiers, population,
route, duration and source. A basis resolved by numeric-text matching alone is a
candidate for review, not automatic approval for consumer guidance. Distinguish
daily/chronic limits from single-dose endpoints. Do not calculate safe product portions
without actual ingredient concentration; do not invent sex-specific thresholds.

## Reuse and freshness check

Consult official EFSA/OpenFoodTox distribution pages and applicable reuse terms.
Record URLs, access date, attribution requirements and any third-party exceptions.
Do not infer permission for journal full-text redistribution from dataset access.
For pilot substances check official assessment references for superseding opinions
where feasible; record checked scope and unresolved freshness rather than claim
exhaustive currency. External source checks are separate from raw dataset extraction.
If access or rights remain unclear, report the limitation; continue offline work but
do not mark profiles publication-ready.

## Tests and deliverables

Test exact/normalized identifiers, suffix distinctions, conflicting E/CAS, ranges,
missing identifiers, multiple assessments, subject linkage, truncated records and
repeatability. Include small synthetic fixtures without bulk dataset redistribution.
Run focused tests and full backend suite with pinned dependencies; report exact
commands and outcomes. Use only disposable infrastructure if needed.

Commit code/tests and docs/OPENFOODTOX_PILOT_REPORT.md containing counts, matching
rules, sample review drafts, limitations, source links, reuse/freshness findings,
and exact commands/output paths. Keep bulk generated evidence outside Git. Update
docs/CODEX_HANDOFF.md. Return full pushed SHA, test results and clear integration
blockers. No claim that data is visible in the application: this is an offline pilot.
