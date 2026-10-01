# OpenFoodTox audit: follow-up task for Claude

Date: 2026-10-01. Reviewed baseline: 8dd62355281c147bc84c9266341a47b60a509846.
Status: requested; not implemented by this documentation commit.

Continue on `feat/backend-openfoodtox-dataset-audit` in an isolated worktree.
Read applicable repository instructions and fetch this document before starting.
Do not modify the live checkout, originals, database, API, Health Score, or containers.
No merge or deployment. Commit and push scoped fixes/tests/documentation to this branch,
without force-push. Preserve unrelated changes and check remote drift first.

## 1. E-number suffixes (confirmed code defect)

`scripts/openfoodtox/records.py::derive_reference_substance` accepts an E-number
only when the part after E is entirely numeric. Thus explicit synonyms such as
E150d are not captured in the derived E-number field (the raw synonym can survive).

Implement conservative full-token recognition including letter suffixes, case and
spacing normalization. Preserve the original synonym and its provenance. Never
collapse E150a/E150b/E150c/E150d into E150. Preserve distinct explicit identifiers
and flag conflicts rather than choosing the first silently. Reject arbitrary words
and malformed codes; recognition is not proof of regulatory authorization.
Add regression tests for numeric, suffixed, spaced, lowercase, invalid and conflicting
identifiers. Report changed coverage after rerunning extraction.

## 2. Bounded extraction versus completeness (confirmed mismatch)

`MAX_RAW_FIELDS_PER_DOCUMENT = 400` stops the leaf walk. Derived views consume this
bounded list, so later identity/evidence/reference-value fields can be omitted.
`raw_fields_truncated` exists, but documentation claims all fields are preserved.
Exactly 400 collected leaves currently also sets the flag without proving omission.

Measure the full dataset: affected documents, dossiers, types and affected derived
fields. Make truncation detection accurate. Prefer complete bounded/streaming
extraction with resource safeguards. If a limit remains, explicitly quarantine
incomplete derived records from usable evidence and surface incompleteness in every
summary/profile, including E250. Do not merely raise the cap and claim completeness.
Test below/at/above the boundary with critical fields beyond the old limit. Correct
the documentation and rerun the catalogue and audits in a new versioned output folder.
Keep originals and previous outputs recoverable.

## 3. E250 reference-value chemical basis (verification request, not a proven error)

The report presents ADI 0.1 mg/kg bw/day without an explicit chemical expression
basis. Inspect the actual source record, linked justification and cited EFSA opinion
to determine whether values refer to sodium nitrite, nitrite ion, or another basis.
Consult the official cited opinion if needed; keep external verification separate
from raw IUCLID extraction and record exact source locations and dates.

Preserve original value, unit, chemical basis, qualifiers and population. Do not
silently convert, replace or assume equivalence. If unresolved, mark the basis
unresolved and unsuitable for consumer intake guidance. If sources differ, explain
the discrepancy instead of picking one. Add tests that prevent conflating bases.

## Delivery

- Run focused extractor tests and the full backend suite with pinned dependencies;
  report exact commands/results and any skips or limitations.
- Rerun the offline full-dataset extraction/audit and regenerate E250's profile.
- Update `docs/OPENFOODTOX_DATASET_AUDIT.md` and `docs/CODEX_HANDOFF.md` with findings,
  output locations, counts, remaining limitations and next steps.
- Keep bulk data/archives/generated catalogues outside Git. Only code, small tests,
  documentation and a concise results summary belong in the commit.
- Return full pushed SHA. These fixes do not authorize live integration or release.

Optional independent subagent review is permitted for parser correctness and
scientific source/basis verification. Do not substitute model-generated claims for
the source evidence.
