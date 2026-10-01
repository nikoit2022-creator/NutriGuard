# Claude: offline pilot review corrections

Date: 2026-10-01. Reviewed commit: 4db31ecbaaae5c113398004577748ae7046080f6.
Continue on the same branch in an isolated worktree. Read applicable instructions,
OPENFOODTOX_PILOT_TASK.md and OPENFOODTOX_PROFILE_PRESENTATION.md. All original
offline-only restrictions remain: no live database/checkout changes, API or Android
changes, Health Score changes, merge or deploy. Scoped commit and non-force push
to feat/backend-openfoodtox-dataset-audit are authorized.

## 1. Correct source identity in freshness research

The report identifies DOI 10.2903/j.efsa.2020.6032 as a dedicated E330 re-evaluation.
Codex checked https://www.efsa.europa.eu/en/efsajournal/pub/6032 on 2026-10-01:
its title, abstract and summary identify E472a-f (esters of mono-/diglycerides),
including citric-acid esters E472c, not a standalone re-evaluation of E330.
Remove the unsupported claim that this establishes a newer missing E330 assessment.
Correct both report and current handoff statements, preserving an explicit correction
trail. Recheck official sources using exact substance identifiers and assessment
scope; otherwise mark freshness unresolved. Mentioning a substance in a paper is
not proof it is the assessed additive. Audit the other three pilot references too.
Keep a source-check table: DOI/title, actual assessed identifiers, relevance, date,
and superseding-status evidence. No guessed replacement reference.

## 2. Incomplete evidence must not be eligible

evidence_bundle._review_eligible currently checks basis/population/unit but not
evidence_complete. Make completeness fail-closed, including missing flags and
incomplete identity/subject linkage. Review what review_eligible actually means:
clearly distinguish eligible for operator inspection from eligible for numeric
human intake guidance. Reference-value type, population, route and period must be
applicable; AOEL/operator or feed levels must not pass as consumer daily limits.
Preserve internal rejected records and reason codes; never promote scientific or
translation status. Add regression tests for truncated records with otherwise
valid basis/units, missing completeness, unresolved links, and non-consumer values.

## 3. Unrelated identities are not conflicts

matcher._compare_one returns conflicting whenever both identifier types are present
and they do not both agree, including pairs where NEITHER agrees. Fix the truth table:
- both agree: exact candidate;
- exactly one agrees and the other conflicts: conflicting, not auto-resolved;
- neither agrees: unrelated/no hit;
- only one comparable: exact if equal, otherwise unrelated (retain existing ambiguity
  and qualified/range protections).
Test a query with both CAS/E-number against a stream containing its exact identity,
an actual one-identifier conflict and many unrelated fully identified records.
Verify meaningful counts and no false conflicts from unrelated records.

## 4. Supersede stale derived data reproducibly

The AssessmentBody numeric-value fix is important, but staging/v3 still contains
the bad derived values. Preserve v3; regenerate a new versioned full catalogue and
reports with corrected code, then run the pilot against that version. Document v3
as unsuitable for evidence consumption. Record actual extractor schema/version,
input hashes and producing code revision; a leaf-cap constant alone is not a schema
version. Do not label an output with a clean SHA while its producing code was dirty
unless the dirty state and exact source fingerprint are also recorded.
Compare numeric changes and recomputed basis statuses dataset-wide. No live import.

## 5. Deliver substantive bilingual drafts for review

Keep original English quotations in an internal evidence section. Separately provide
readable EN and BG paraphrase drafts for each supported claim, with matching certainty,
conditions and claim-to-source links. Do not present English quotations as completed
BG descriptions. Omit unsupported origin/function sections, but retain omitted-field
notes internally. Both languages remain DRAFT/not_reviewed; no runtime localization
writes and no assertion of publication readiness. Commit concise sample drafts for
all four pilot identities in the report or a dedicated small review document so
Codex can review them through Git, without needing the VM's bulk output directory.

## Verification and delivery

Run focused regression tests and full suite with pinned dependencies. Rerun full
offline extraction and pilot, compare repeatability excluding explicitly volatile
metadata, and report commands/counts/changes honestly. Update pilot report, source
checks and CODEX_HANDOFF. Push scoped code/tests/docs; bulk datasets stay outside Git.
Return full SHA and remaining blockers. This task does not authorize integration,
automatic safety claims, seed mutation, merge or deployment.
