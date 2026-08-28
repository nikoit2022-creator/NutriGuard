# CLAUDE.md — NutriGuard Backend

This file defines how Claude Code operates as the **primary development
agent for the NutriGuard backend**. It applies to everything under
`nutriguard-backend/`.

## 0. Role separation

- **AI Studio** owns the Android application (native Kotlin + Jetpack
  Compose client). Claude Code does not develop it.
- **Claude Code** owns backend development: the FastAPI service in this
  directory — features, fixes, tests, docs, investigation.
- **Codex** is brought in for surgical/targeted fixes only, when
  explicitly requested. See section 13 for how to hand work to it.
- The Git repository is the source of truth.
  GitHub is the shared remote, review, and coordination point. Local
  branches and working-tree state are not authoritative until pushed and
  reviewed there.

## 1. Repository / backend location

- Git root: `the repository root` (the repo also contains the
  Android client source and archival material outside this directory —
  those are out of scope for backend work; see section 11).
- Backend root: `nutriguard-backend/` — treat this as the working
  directory for all backend commands (`docker compose`, `pytest`,
  `alembic`, `uvicorn`, etc.).
- The API contract (`NutriGuard_API_Contract.md`) lives on disk under
  `files/` at the git root but is **not** git-tracked (excluded by the
  root `.gitignore`). Treat it as the reference spec when present, but
  be aware it may not exist in every checkout/branch.

## 2. Backend architecture and layering

FastAPI **modular monolith**. Strict layering, top to bottom:

```
app/api/v1/        FastAPI routers (one file per resource) — HTTP only
app/services/       business logic (health score, warning engine,
                    OCR normalizer, fallback/Gemini analysis)
app/repositories/   DB access layer — no business logic
app/models/         SQLAlchemy ORM models
app/schemas/         Pydantic request/response schemas (camelCase, see §7)
app/integrations/   external services — only Gemini today
                    (`gemini.py` is the sole module allowed to call it)
app/database/        engine/session/declarative base
app/seed/            scientific ingredient seed data + idempotent loader
app/core/            config, JWT security, exceptions, logging, rate limiting
app/main.py          app factory, exception handlers, middleware
alembic/              Postgres migrations
tests/unit/           pure business-logic tests, no DB/network
tests/integration/     full HTTP request/response tests (httpx + in-memory SQLite)
```

Routers call services, services call repositories, repositories touch
the DB. Do not put business logic in routers or repositories, and do
not let routers or services import from `app/integrations/` except
through the service layer that already wraps Gemini.

## 3. Primary technologies

FastAPI, SQLAlchemy 2.x (async, `asyncpg` in prod), Alembic, PostgreSQL
16, Redis 7 (cache + rate limiting), Pydantic 2 / `pydantic-settings`,
JWT via `python-jose` (HS256), `structlog`, `slowapi` (rate limiting),
`httpx`, `pytest` / `pytest-asyncio` / `aiosqlite` for tests, Docker /
Docker Compose for orchestration.

## 4. Running the backend (Docker — normal path)

```bash
cd nutriguard-backend
cp .env.example .env
# edit .env: set JWT_SECRET, and GEMINI_API_KEY if real AI analysis is wanted
docker compose up --build
```

This starts `db` (Postgres 16), `redis` (Redis 7), `backend` (FastAPI,
`--reload`). On startup the backend container automatically waits for
Postgres, runs `alembic upgrade head`, and seeds the scientific
ingredient database (idempotent, safe on every restart).

- Docs: `http://localhost:8000/api/v1/docs` (Swagger) /
  `http://localhost:8000/api/v1/redoc`.
- Health check: `http://localhost:8000/health`.
- Production overlay:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.

Bare-metal alternative (no Docker) is documented in `README.md` section
2 — use it only when Docker isn't available; it requires a real
Postgres the caller controls.

## 5. Running tests

```bash
cd nutriguard-backend
source .venv/bin/activate   # only if a project venv exists/has been set up
pytest -q
```

The suite runs against an in-memory SQLite DB via `aiosqlite` — no
Docker or Postgres required, no external network calls (Gemini is
forced off via `GEMINI_API_KEY=""` in `tests/conftest.py`, so
Gemini-dependent code paths exercise the deterministic fallback).
Postgres-specific behavior (migrations) is validated separately against
a real Postgres instance, not by `pytest`.

Rules for Claude Code specifically: see section 12 — never report a
test result without having actually run the command in this session.

## 6. Coding conventions

- Follow the existing layering (section 2) — don't bypass it for
  convenience.
- Match existing style in the file being edited (naming, docstring
  density, error handling patterns) rather than introducing a new style.
- Every domain error is an `AppError` subclass in `app/core/exceptions.py`
  with a fixed `code`/`status_code`; new error cases should follow the
  same pattern rather than raising raw `HTTPException`.
- Secrets are only ever read from environment variables via
  `app/core/config.py` (`pydantic-settings`) — never hard-code a secret
  or default it to a real-looking value.
- Structured logging via `structlog`; don't `print()` in application
  code.
- New endpoints/services need corresponding tests in `tests/unit/` and/or
  `tests/integration/` following the existing file-per-concern pattern.

## 7. API contract / camelCase rule

- All Pydantic request/response schemas (`app/schemas/`) use **camelCase**
  field names (via the shared `ORMModel` base) to match the Android
  client's JSON exactly. Never introduce snake_case fields on the wire.
- The authoritative source for endpoint shapes, error codes, and
  behavior is `NutriGuard_API_Contract.md` (see section 1) when present;
  otherwise treat `README.md` section 4 (endpoint table) and
  `openapi.json` as the current contract snapshot.
- `openapi.json` at the backend root is a checked-in static snapshot for
  diffing — when a change alters the generated OpenAPI schema, regenerate
  and diff it, and call that out explicitly rather than leaving it stale
  silently.

## 8. Contract-deviation rule

If backend behavior must intentionally diverge from the API contract
(or from the Android client's original behavior), it must be:

1. **Documented** — add an entry to `README.md` section 6 ("Deviations
   from the API Contract") explaining what changed, why, and what
   alternative was considered.
2. **Tested** — add a regression test that pins the new (intentional)
   behavior, named so its purpose is obvious.

Never silently resolve an ambiguity or "fix" a contract mismatch without
both of the above. Several existing deviations (e.g. Gemini's parsed
nutrition fields being intentionally discarded in favor of deterministic
fallback recomputation; certain health-profile flags being accepted but
inert) are deliberate, tested behavior — do not "fix" these without an
explicit product-owner decision to change the contract first.

## 9. Security rules

- Never expose, print, log, or commit the contents of `.env`,
  `client_secret.json`, or any other secret/credential file.
- Never modify `.env`, `.env.local`, or any credentials file — if a
  configuration value needs to change, document the required env var in
  `.env.example` (with a placeholder, never a real value) and tell the
  user to update their own `.env`.
- Never put passwords, API keys, tokens, secrets, or `.env` values into
  any documentation, commit message, PR description, or handoff file.
- If a secret-related issue matters for a report, describe it
  generically (e.g. "GEMINI_API_KEY appears unset") without exposing the
  value.
- Treat `.gitignore` entries for secrets/credentials as load-bearing —
  never remove or narrow them.

## 10. Git safety

- Do not `reset`, `rebase`, force-push, or otherwise discard user
  changes (staged, unstaged, or committed) unless explicitly instructed
  to in that specific request.
- Do not commit changes unrelated to the task at hand — keep commits
  scoped to what was asked.
- Do not push to any remote unless explicitly requested.
- Do not switch branches or create new branches unless explicitly
  requested.

## 11. Android / backend boundary

- Do not modify Android/Kotlin source (wherever it currently lives in
  the repo) unless explicitly requested. Android app development is AI
  Studio's responsibility, not Claude Code's.
- When investigation surfaces an Android/backend contract mismatch or
  integration issue, report it precisely rather than fixing the Android
  side:
  - the exact endpoint (method + path),
  - the request/response contract as currently specified vs. as
    actually implemented/observed,
  - the backend-side finding (what the backend does, and file/line if
    relevant),
  - what would need to change, and on which side, without making that
    change to Android code yourself.

## 12. Testing requirements

- After making a backend change, run the relevant tests
  (`pytest -q`, or a narrower `pytest tests/unit/test_x.py` /
  `tests/integration/test_y.py` for a targeted change) before reporting
  the change as done.
- Always report the exact command run and its actual result (pass/fail
  counts, or the failure output) — never summarize vaguely.
- Never claim tests passed, or that a suite is "green," without having
  actually executed it in this session. If tests could not be run
  (missing venv/deps, no Docker, etc.), say so plainly instead of
  guessing at the outcome.

## 13. Coordination with Codex

Some problems are better suited to a small, surgical, targeted edit
than to broader Claude Code-driven development — use Codex for those
when explicitly requested. When handing off such a case, provide:

- **File**: exact path.
- **Location**: function/class/line (or a precise anchor if line numbers
  aren't stable).
- **Problem**: what is wrong, concretely (not just "this seems off").
- **Expected behavior**: what correct behavior looks like.
- **Recommended change**: the specific, minimal edit — not a broad
  refactor.

This keeps Codex's edits scoped and reviewable, consistent with its
"surgical fix" role.

## 14. Existing repository instructions

- `AGENTS.md` and `docs/CODEX_HANDOFF.md` are **Codex-specific**
  documentation: `AGENTS.md` defines the required task flow for Codex
  agents (read both files at task start, update the handoff at task
  end), and `CODEX_HANDOFF.md` is Codex's running handoff log.
- Claude Code is **not** responsible for updating
  `docs/CODEX_HANDOFF.md` as part of normal work, and should not do so
  unless the user explicitly asks for it. Reading it for context is
  fine and can be useful background before a task.
