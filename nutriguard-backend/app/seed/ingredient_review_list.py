"""Manual server command: python -m app.seed.ingredient_review_list.

No public endpoint, network translation, scheduled job or database writes.
--output atomically replaces ONE UTF-8 JSON snapshot, not a growing log.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile


async def read_report():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.core.config import settings
    import app.models  # noqa: F401 -- register relationships
    from app.services.ingredient_review_list import build_review_list

    options = {"isolation_level": "REPEATABLE READ"} if settings.DATABASE_URL.startswith("postgresql") else {}
    engine = create_async_engine(settings.DATABASE_URL, echo=False, **options)
    try:
        async with async_sessionmaker(engine)() as session:
            if engine.dialect.name == "postgresql":
                from sqlalchemy import text
                await session.execute(text("SET TRANSACTION READ ONLY"))
                await session.execute(text("SET LOCAL statement_timeout = '15s'"))
                await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            try:
                return await build_review_list(session)
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


def write_snapshot(path: Path, content: str):
    """Leave the last valid snapshot intact if generation or writing fails."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".ingredient-review-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Replace this UTF-8 JSON snapshot (directory must exist).")
    args = parser.parse_args()
    try:
        report = asyncio.run(read_report())
        content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            write_snapshot(args.output, content)
        else:
            sys.stdout.reconfigure(encoding="utf-8")
            print(content, end="")
    except Exception:
        # Never expose connection strings, SQL parameters, or ingredient names
        # on an error; a failure is not an empty successful review list.
        print("Ingredient review export failed; check database/schema and output permissions.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
