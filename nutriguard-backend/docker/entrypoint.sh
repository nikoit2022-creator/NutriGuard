#!/bin/sh
set -e

echo "Waiting for database..."
python - <<'PYEOF'
import asyncio
import sys
import time

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings


async def wait():
    engine = create_async_engine(settings.DATABASE_URL)
    for attempt in range(30):
        try:
            async with engine.connect():
                print("Database is ready.")
                return
        except Exception as exc:  # noqa: BLE001
            print(f"DB not ready yet ({exc}); retrying ({attempt + 1}/30)...")
            await asyncio.sleep(2)
    print("Database never became ready.", file=sys.stderr)
    sys.exit(1)


asyncio.run(wait())
PYEOF

echo "Running Alembic migrations..."
alembic upgrade head

if [ "${SEED_ON_START:-true}" = "true" ]; then
    echo "Seeding scientific ingredient database (idempotent)..."
    python -m app.seed.load_seed || true
fi

echo "Starting application..."
exec "$@"
