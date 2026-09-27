"""
Manual, dry-run-by-default maintenance for the ingredient candidate queue
(issue #23, stage 2). Never scheduled, never run at startup.

    python -m app.seed.ingredient_candidate_maintenance prune              # dry run (default)
    python -m app.seed.ingredient_candidate_maintenance prune --apply
    python -m app.seed.ingredient_candidate_maintenance backfill           # dry run (default)
    python -m app.seed.ingredient_candidate_maintenance backfill --apply

`prune` lists (or, with --apply, removes) stale, rarely seen, unflagged
queue rows. `backfill` seeds queue rows for existing uncurated catalog
identities. Both touch ONLY `ingredient_candidates`: never an
`ingredients` row, an alias, or a product reference. The report prints
counts and row ids/flags only, never ingredient text.
"""
import argparse
import asyncio
import json

from app.database.session import AsyncSessionLocal
from app.services import ingredient_candidates


async def main(command: str, *, apply: bool) -> dict:
    async with AsyncSessionLocal() as session:
        if command == "prune":
            plan = await ingredient_candidates.prune(session, dry_run=not apply)
            report = {
                "command": "prune",
                "dry_run": plan.dry_run,
                "matched": len(plan.candidates),
                "removed": plan.removed,
                "rows": [
                    {"id": i, "status": status, "flags": flags, "encounterCount": count}
                    for i, status, flags, count in plan.candidates[:100]
                ],
            }
        else:
            plan = await ingredient_candidates.backfill_from_catalog(session, dry_run=not apply)
            report = {
                "command": "backfill",
                "dry_run": plan.dry_run,
                "wouldCreate": plan.would_create,
                "created": plan.created,
                "alreadyPresent": plan.already_present,
            }
        if apply:
            await session.commit()
        else:
            await session.rollback()
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manual maintenance for the ingredient candidate queue.")
    parser.add_argument("command", choices=["prune", "backfill"])
    parser.add_argument("--apply", action="store_true", help="Write changes. Default is a dry run with no writes.")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(main(args.command, apply=args.apply)), indent=2))
