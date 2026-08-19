# Repository Instructions for Codex Agents

Scope: this file applies to the entire repository rooted at this directory.

Required task flow:

1. At the start of every task, read `AGENTS.md` and `docs/CODEX_HANDOFF.md` before making changes.
2. At the end of every task, update `docs/CODEX_HANDOFF.md`.

`docs/CODEX_HANDOFF.md` must be updated with:

- what was investigated or changed
- files involved
- commands/tests run and their results
- unresolved issues
- the exact recommended next step

Security constraints:

- Never put passwords, API keys, tokens, secrets, or `.env` values in `AGENTS.md`.
- Never put passwords, API keys, tokens, secrets, or `.env` values in `docs/CODEX_HANDOFF.md`.
- If a secret-related issue matters for handoff, describe it generically without exposing the value.

Repository convention for this workspace:

- Treat this directory as the repository root even if `.git` metadata is unavailable in the current environment.
