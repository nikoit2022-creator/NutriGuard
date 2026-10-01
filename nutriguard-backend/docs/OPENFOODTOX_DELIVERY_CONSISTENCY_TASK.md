# OpenFoodTox: close the two remaining delivery gaps

Assigned by the owner via Codex, 2026-10-01. Reviewed baseline:
`8ceba0531082e2c63cbe62598af663c9f852ece9`.
Continue on `feat/backend-openfoodtox-dataset-audit` in an isolated worktree.
Read repository instructions and the latest handoff first. Fetch and check
remote drift; preserve other contributors' work.

## Scope and delegation

Finish only the two items below. The owner explicitly permits subagents
if useful: for example one for source tracing and another for generated
document consistency. You remain responsible for integration and review.
Do not redesign extraction, expand the pilot beyond its four identities,
or revisit already-fixed items without a reproducible regression.

## 1. Make the committed previews match actual generated output

The code now translates headings and fixes `каква точно количество`, but
`docs/OPENFOODTOX_PILOT_REVIEW_DRAFTS.md` still contains the old phrase,
English headings in Bulgarian previews, and active `pilot/v4` references
despite claiming to reproduce `pilot/v5` from `b051e2a`.

- Regenerate the pilot against unchanged staging/v4 into a new versioned
  output directory, preserving previous outputs.
- Export all four complete EN/BG preview bodies directly from that output
  into the committed review document, without hand-editing generated prose.
- Provide a reproducible export/check command or small script, and a check
  that all eight committed bodies equal the corresponding JSON draft
  fields (allow only documented Markdown wrapper/line-ending changes).
- Correct current-output references and producing-code provenance in the
  report; historical references can remain clearly labeled as historical.
- Keep internal evidence/operator notes separate from consumer previews.

## 2. Complete primary-source traceability, not just seed attribution

The new matrix still attributes consumer claims solely to our seed files.
Examples: E250-04 human evidence, E250-05 nitrosation/risk discussion,
E951-17 digestion/metabolism. Its audit note says effect conclusions never
rely solely on seeds, contradicting these entries. A local seed is content
origin, not independent evidence. Other identity/purpose claims also need
the concrete support requested by the prior task.

- For each consumer editorial claim, retain content origin separately and
  add its actual supporting primary citation: DOI/URL/document identity,
  section/page/table or a short locator, access date, and what it supports.
- Read the cited passage. Do not replace a seed filename with an unchecked
  DOI, topic homepage, or a secondary report labeled as primary evidence.
- Check the newly asserted E330 Regulation definition against the actual
  primary text as well; do not assume the prior report's quote is correct.
- Narrow wording to the evidence. If support cannot be established, retain
  the claim internally for review and omit it from the consumer preview.
  No invented replacement content or empty boilerplate.
- Regenerate the committed claim/source matrix from the same data used by
  the renderer. Include usable citations/locators without truncating away
  the identifying information. Keep scientific/translation status draft;
  source tracing by an agent is not independent scientific approval.
- Add focused tests for provenance completeness and withholding unsupported
  claims; tests prove policy/structure, not scientific truth.

## Verification and delivery

Run focused tests and the full backend suite, recording exact commands and
counts. Check generation repeatability and eight-body document equality.
Update `docs/CODEX_HANDOFF.md` and `docs/OPENFOODTOX_PILOT_REPORT.md` with
results, actual remaining limitations, source checks and output paths.
Commit and push only this branch, normally (no force-push). Return full SHA
and concise completion report. No merge, deploy, live DB access, seed import,
API/Android/Health Score changes, secrets edits or live container operations.
Do not regenerate bulk extraction or commit bulk datasets/build artifacts.
