# Repository Instructions

Scope: the entire NutriGuard repository.

## Source of truth

- GitHub `main` is the canonical version.
- Work in a task-specific branch and merge through review.
- Recovery branches are archival inputs; do not merge them wholesale.
- Do not use untracked local files as an API contract or project specification.

## Required workflow

1. Read this file and any more specific instructions in the subproject being changed.
2. Keep Android work inside `android-app/` and backend work inside `nutriguard-backend/`.
3. Do not mix unrelated Android, backend and repository-maintenance changes.
4. Run the relevant tests and report their exact results.
5. Do not push, merge, delete branches or modify `main` without explicit authorization.

## Safety

- Never commit secrets, `.env`, credentials, keystores, `local.properties`, databases or build outputs.
- Do not reset, rebase, force-push or discard another contributor's work without explicit authorization.
- Preserve the Android/backend wire contract: JSON fields are camelCase.
- Intentional API-contract changes must be documented and covered by regression tests.

## Verification

- Android: `cd android-app && ./gradlew testDebugUnitTest`
- Android on Windows: `cd android-app; .\gradlew.bat testDebugUnitTest`
- Backend: `cd nutriguard-backend && python -m pytest -q`
