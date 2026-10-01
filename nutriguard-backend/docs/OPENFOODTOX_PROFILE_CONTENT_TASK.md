# Claude task: complete four source-grounded EN/BG review profiles

Date: 2026-10-01. Baseline: 40d063444358086c2b47dbe1a2a7386b9509349d.
This is the active next task; prior review tasks are historical context.
Read applicable repository instructions and OPENFOODTOX_PROFILE_PRESENTATION.md.
Continue in an isolated worktree on feat/backend-openfoodtox-dataset-audit.
Scoped commits and non-force push are authorized. No live DB/checkout/container
changes, app/API/seed changes, scientific status promotion, merge or deployment.

## Outcome

Deliver four substantive, source-linked EN/BG drafts (E250, E150d, E330, E951)
for product review through Git. The current drafts mostly restate numbers; they
do not yet explain what the ingredient is, its food function, or relevant effects.
Keep original extracted records intact. Use a separately versioned offline editorial
layer if needed, with explicit claim-to-source mapping. Do not generate authoritative
health descriptions from numeric fields alone or use live LLM calls in tests.

## Required content corrections

1. E951: the source explicitly excludes people with phenylketonuria (PKU) from
   applicability of the stated ADI. The current paraphrase leaves that only in an
   internal quote. Carry this limitation into BOTH language drafts whenever the ADI
   or general-population safety conclusion is shown. Keep it adjacent, not hidden
   under Sources. Do not turn this into personalized medical advice.
2. E150d: preserve the source's GROUP ADI qualifier. Do not represent it as an
   independent allowance for each caramel colour. Verify precisely which substances
   the group covers and any separate subgroup limits against the original opinion;
   retain uncertain chemical basis as unresolved rather than guess it.
3. Date correction: official DOI 10.2903/j.efsa.2026.10259 states "First published:
   10 September 2026" and "Approved: 1 July 2026". Correct references claiming it
   was adopted on September 10. Keep publication and approval dates distinct.
   Describe the specific updated E951 assessment within the E962 opinion rather
   than claim that every part of the 2013 opinion was formally superseded.
4. BG: translate ordinary terms and units readably, e.g. натриев нитрит and
   мг/кг телесно тегло дневно where exactly equivalent to the source. Preserve original
   machine-readable units and names internally. Source titles may remain original
   under Sources, but should not overwhelm each paragraph. No English population
   labels or placeholders in the Bulgarian consumer-preview body.

## Four-profile structure

- Name / E-code.
- What it is and origin/manufacturing context, only where actually supported.
- Purpose in food.
- Relevant effects, including digestive effects only if supported. Explain exposure
  conditions and distinguish human evidence, animal/in-vitro findings and assessment
  conclusions. Uncertain evidence must retain its uncertainty; avoid causal overclaims.
- Intake information only when adequately supported with type, chemical basis,
  period, group scope and population exceptions. ADI is not a target intake or a
  product portion. No inferred safe portion or sex-specific threshold.
- Informational source links at the bottom; claims remain traceable internally.

Use primary sources: official EFSA opinions and specifications where relevant.
External sources are authorized for this limited four-profile content task, but must
remain distinguishable from imported OpenFoodTox evidence. Read actual scope/full
relevant passages, not search snippets or similarity of titles. Cite exact sections,
publication dates, access dates and paraphrase basis. Omit unsupported content instead
of filling gaps. Keep feed/worker guidance out of the consumer preview, retaining it
only in operator evidence. Do not use absence of a numeric ADI to imply unlimited use.

Verified review reference pointers (independently check the relevant passages):
- https://www.efsa.europa.eu/en/efsajournal/pub/10259
- https://www.efsa.europa.eu/en/plain-language-summary/re-evaluation-salt-aspartame-acesulfame-e-962-food-additive
- The existing caramel-colour opinion 10.2903/j.efsa.2011.2004 and exposure update
  10.2903/j.efsa.2012.3030; do not conflate exposure updates with new ADI derivations.

## Display gating and regression checks

The current template can display a numeric value in "Effects and conditions" even
while hiding "Intake guidance". Moving a number between headings must not bypass
the same publication/eligibility rules. Separate operator evidence from the proposed
consumer preview. All text remains DRAFT/not_reviewed; no production approval.
For the offline preview, attach required qualifications to every shown claim and
exclude ineligible numeric guidance from every consumer-facing section.

Test preservation of PKU exceptions, group scope, and date types; numeric gating
across sections; EN/BG claim parity and source references. Tests should verify
structure and required qualifiers, not assert scientific truth just because a fixture
contains a string. If modifying tooling, run focused and full pinned-dependency tests.
Do not rerun the whole dataset merely for prose changes unless extraction changed.

## Delivery

Commit all four complete EN/BG draft profiles in a concise review document (update
OPENFOODTOX_PILOT_REVIEW_DRAFTS.md), with a separate internal claim/source matrix and
remaining uncertainties. Update the pilot report and CODEX_HANDOFF, including date
corrections. Record exact tests, source checks, code/content versions and output paths.
Return full pushed SHA. No automatic VERIFIED/REVIEWED promotion or publication;
Codex will review the actual texts before any application integration.
