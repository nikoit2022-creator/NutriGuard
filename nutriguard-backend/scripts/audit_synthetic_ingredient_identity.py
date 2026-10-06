"""
Read-only audit: find PERSISTED products whose synthetic ingredient id(s)
no longer round-trip under the CURRENT `ocr_normalizer` tokenization/
hashing logic.

Why this exists (task: "existing damaged records" -- fix/backend-
cyrillic-e-number): `Ingredient.id` for an OCR-derived ingredient with
no scientific-database match is a deterministic hash of its (processed)
name text (see `app.services.ocr_normalizer._synthetic_id`).
`fetch_ingredients_for_product`/`reconstruct_synthetic_ingredient`
recover that name ONLY by re-tokenizing `Product.raw_ingredient_text`
with TODAY's code and recomputing the SAME id. Any change to
tokenization/normalization (this branch's Cyrillic-E fix among them --
see `ocr_normalizer._CYRILLIC_E_BEFORE_NUMBER`, and the earlier,
similarly-shaped fix already documented in `reconstruct_synthetic_
ingredient`'s own docstring) changes what id a given raw token hashes
to. A product whose `ingredient_ids` were persisted under OLDER code is
not retroactively rewritten -- the NEXT read reconstructs it with
TODAY's code, and if that no longer reproduces the same id, the row
silently degrades (to a less-readable slug-based guess, a bare number
with no identity, or the generic "Ingredient detected on label"
placeholder), with NO error raised anywhere in the normal request path.

This script finds every such row WITHOUT changing anything: it only
runs `SELECT`s, and the one transaction it opens is always rolled back,
never committed, even on success. It never infers an E-number from a
bare number, never rewrites `ingredient_ids`/`raw_ingredient_text`, and
never deletes anything -- it only reports what it finds so a human can
decide what (if anything) to do next.

Usage:
    python -m scripts.audit_synthetic_ingredient_identity \\
        --database-url postgresql+asyncpg://user:pass@host/db \\
        [--limit N] [--json]

If `--database-url` is omitted, falls back to `settings.DATABASE_URL`
(the same variable the app itself uses) -- which, in a normal
developer/CI setup, points at a local/disposable database, NOT a
production one. Pointing this at a live/production database is the
caller's explicit choice; the script itself only ever reads.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.ingredient import Ingredient
from app.models.product import Product
from app.services.ocr_normalizer import (
    SyntheticIdentityMatch,
    reconstruct_synthetic_ingredient,
    resolve_synthetic_identity,
)

_BARE_NUMBER = re.compile(r"^\d+$")
_PLACEHOLDER_NAME = "Ingredient detected on label"


@dataclass(frozen=True)
class AuditFinding:
    barcode: str
    stored_ingredient_id: str
    reconstructed_id: str
    reconstructed_common_name: str
    category: str
    raw_ingredient_text_excerpt: str


def _confidently_recoverable(ingredient_id: str, raw_text: str) -> bool:
    """True when `resolve_synthetic_identity` matches `ingredient_id` to a
    token of the stored text -- under today's id OR a legacy id older code
    minted (Cyrillic-E / Bulgarian-alias), which reads back correctly and
    is therefore not damage. AMBIGUOUS and NONE stay findings.

    NOTE this is deliberately NOT a comparison of
    `reconstruct_synthetic_ingredient(...).id` to `ingredient_id`: its
    fallback branch always overwrites the result's `id` with the stored
    one, so that comparison is always True."""
    return resolve_synthetic_identity(ingredient_id, raw_text).match not in (
        SyntheticIdentityMatch.NONE,
        SyntheticIdentityMatch.AMBIGUOUS,
    )


def _classify_fallback_recovery(reconstructed_common_name: str) -> str:
    """Only called once `_confidently_recoverable` is already False -- i.e.
    `reconstruct_synthetic_ingredient` fell through to its legacy
    slug/hash fallback. Categorizes WHAT it recovered, worst first, for
    a human reviewer to triage; never a repair decision on its own."""
    if reconstructed_common_name == _PLACEHOLDER_NAME:
        return "PLACEHOLDER_LOST_IDENTITY"
    if _BARE_NUMBER.fullmatch(reconstructed_common_name.strip()):
        return "BARE_NUMBER_NO_IDENTITY"
    return "RECOVERED_BUT_ID_UNSTABLE"


async def _audit(session: AsyncSession, *, limit: int | None) -> list[AuditFinding]:
    stmt = select(Product).where(Product.ingredient_ids.is_not(None), Product.ingredient_ids != "")
    if limit is not None:
        stmt = stmt.limit(limit)
    products = (await session.execute(stmt)).scalars().all()

    findings: list[AuditFinding] = []
    for product in products:
        ids = [i for i in product.ingredient_ids.split(",") if i]
        if not ids:
            continue
        real_rows = (
            (await session.execute(select(Ingredient.id).where(Ingredient.id.in_(ids)))).scalars().all()
        )
        real_id_set = set(real_rows)
        for ingredient_id in ids:
            if ingredient_id in real_id_set:
                continue  # resolves to a real catalog row -- not synthetic, nothing to audit here
            if _confidently_recoverable(ingredient_id, product.raw_ingredient_text or ""):
                continue  # reads back correctly (today's or a recoverable legacy id) -- not a finding
            reconstructed = reconstruct_synthetic_ingredient(ingredient_id, product.raw_ingredient_text or "")
            category = _classify_fallback_recovery(reconstructed.common_name)
            findings.append(
                AuditFinding(
                    barcode=product.barcode,
                    stored_ingredient_id=ingredient_id,
                    reconstructed_id=reconstructed.id,
                    reconstructed_common_name=reconstructed.common_name,
                    category=category,
                    raw_ingredient_text_excerpt=(product.raw_ingredient_text or "")[:160],
                )
            )
    return findings


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL to audit (read-only). Defaults to settings.DATABASE_URL.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Audit at most N products (default: all).")
    parser.add_argument("--json", action="store_true", help="Print findings as JSON instead of a text table.")
    args = parser.parse_args(argv)

    database_url = args.database_url or settings.DATABASE_URL
    engine = create_async_engine(database_url, pool_pre_ping=True, future=True)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    print(f"[audit] READ-ONLY run against: {database_url.split('@')[-1]}", file=sys.stderr)
    print("[audit] no INSERT/UPDATE/DELETE will be issued; the transaction is rolled back, never committed.", file=sys.stderr)

    try:
        async with session_factory() as session:
            if session.bind.dialect.name == "postgresql":
                await session.execute(text("SET TRANSACTION READ ONLY"))
            findings = await _audit(session, limit=args.limit)
            await session.rollback()  # belt-and-suspenders: never commit, even though nothing was written
    finally:
        await engine.dispose()

    if args.json:
        print(json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2))
    else:
        if not findings:
            print("[audit] no mismatched synthetic-ingredient ids found.")
        for f in findings:
            print(
                f"{f.category:28} barcode={f.barcode!r} stored_id={f.stored_ingredient_id!r} "
                f"reconstructed_id={f.reconstructed_id!r} reconstructed_name={f.reconstructed_common_name!r} "
                f"raw_text={f.raw_ingredient_text_excerpt!r}"
            )
        print(f"\n[audit] {len(findings)} finding(s) across potentially affected products.")
        print(
            "[audit] This report does NOT repair anything. 'BARE_NUMBER_NO_IDENTITY' and "
            "'PLACEHOLDER_LOST_IDENTITY' findings lost real identity and need a human decision "
            "(e.g. a backfill migration re-deriving ids with CURRENT code, or a targeted re-scan) -- "
            "never auto-assume what number/E-number a bare digit string originally meant."
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
