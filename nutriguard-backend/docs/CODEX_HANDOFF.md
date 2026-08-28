# CODEX_HANDOFF

## Current canonical state

Date: 2026-08-28

- GitHub `main` is the only source of truth.
- Android source is located at `android-app/`.
- Backend source is located at `nutriguard-backend/`.
- The recovered Gradle 9.3.1 wrapper is tracked.
- Android JVM verification before repository cleanup: 9 tests passed; `BUILD SUCCESSFUL`.
- Recovery branches remain available for audit but must not be merged wholesale.

## Repository cleanup

The cleanup branch normalizes the Android directory name, removes the obsolete tracked ZIP archive, adds repository-level documentation and adds Android/backend CI workflows. Application behavior is intentionally unchanged.

## Unresolved items

- The Android backend base URL is still environment-specific and should be addressed separately.
- The repository does not yet contain a standalone human-authored API contract; `nutriguard-backend/openapi.json` is the tracked wire-schema snapshot.
- Backend CI must confirm the documented test suite against the current dependency set.

## Recommended next step

Run both CI workflows on the cleanup pull request, review the rename as a file move, and merge only when Android and backend verification pass.
