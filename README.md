# NutriGuard

NutriGuard is a food-ingredient analysis platform composed of a native Android client and a FastAPI backend.

## Repository layout

- `android-app/` — Kotlin, Jetpack Compose, Room and Retrofit Android application.
- `nutriguard-backend/` — FastAPI, SQLAlchemy, PostgreSQL, Redis and Docker backend.
- `.github/workflows/` — Android and backend verification on pushes and pull requests.
- `AGENTS.md` — repository-wide collaboration and safety rules.

GitHub `main` is the only canonical source of truth. Local copies and recovery branches are not authoritative until their changes are reviewed and merged.

## Android

```text
cd android-app
./gradlew testDebugUnitTest
```

On Windows:

```powershell
cd android-app
.\gradlew.bat testDebugUnitTest
```

See `android-app/README.md` for application-specific setup.

## Backend

```text
cd nutriguard-backend
python -m pytest -q
```

See `nutriguard-backend/README.md` for environment, Docker and API documentation.

## Security

Never commit `.env`, API keys, tokens, credentials, keystores, `local.properties`, local databases or generated build outputs.
