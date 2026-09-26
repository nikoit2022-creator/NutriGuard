"""
Issue #23 (stage 2): the ingredient candidate queue.

Records, per distinct unknown ingredient token, how often and when it was
seen and which review flags apply -- reusing `ingredients` /
`ingredient_aliases` for identity and never adding evidence (see
`app.models.ingredient_candidate.IngredientCandidate`, which keeps
observation, identity and evidence apart).

Guarantees:

  * Exact only. The queue key is the normalized token text. No fuzzy,
    stemmed or translated similarity ever links two tokens; identity is
    still decided solely by `ingredient_catalog` (official identifier,
    then exact alias).
  * No trust promotion. Seeing a token again (a repeated attempt) only
    raises `encounter_count`. Nothing here writes to `ingredients`.
  * Once per request. A token is counted once per scan request even though
    `materialize_ingredients` runs twice per request; the per-session set
    in `Session.info` is the request scope (sessions are per request).
  * Best effort. A queue failure is logged and swallowed inside a
    SAVEPOINT: it can never fail a scan or undo catalog work.
  * Bounded. Name/key <= 128 chars, flags are a closed vocabulary, rows
    are capped (`INGREDIENT_CANDIDATE_MAX_ROWS`, new rows are refused at
    the cap, nothing is evicted), and `prune` is manual and dry-run by
    default.
  * Stores no image and no label text, only the one token.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import TRUSTED_INGREDIENT_SOURCES, IngredientSource, IngredientVerificationStatus
from app.models.ingredient import Ingredient
from app.models.product import Product
from app.repositories import ingredient_candidate_repository as repo
from app.services import ingredient_candidate_flags as flags_mod
from app.services.ingredient_candidate_flags import CandidateFlag

_logger = structlog.get_logger(__name__)

# `Session.info` key holding the candidate keys already counted for this
# session (= this scan request).
_SEEN_KEY = "ingredient_candidate_keys_seen"


def status_for(flags: set[CandidateFlag] | frozenset[CandidateFlag]) -> str:
    if flags_mod.is_junk(flags):
        return "JUNK"
    return "FLAGGED" if flags else "PENDING"


def merge_flag_strings(existing_raw: str | None, new_raw: str | None) -> tuple[str, str]:
    """Union of two serialized flag sets and the status they imply."""
    merged = set(flags_mod.parse_flags(existing_raw)) | set(flags_mod.parse_flags(new_raw))
    return flags_mod.serialize_flags(merged), status_for(merged)


def reset_request_scope(db: AsyncSession) -> None:
    """Forget which tokens this session already counted (tests and
    long-lived sessions; production sessions are per request)."""
    db.info.pop(_SEEN_KEY, None)


def is_known_identity(ingredient: Ingredient | None) -> bool:
    """A token that resolved to trusted curated/regulatory data is a KNOWN
    ingredient, not a candidate."""
    return ingredient is not None and (
        ingredient.source in TRUSTED_INGREDIENT_SOURCES
        or ingredient.verification_status == IngredientVerificationStatus.VERIFIED
    )


@dataclass
class Observation:
    """One token seen in a scan: its text, an identifier present in the
    text, and the identity row it resolved to (None for junk)."""

    name: str
    e_number: str | None
    resolved: Ingredient | None = None


@dataclass
class ObservationSummary:
    inserted: int = 0
    updated: int = 0
    dropped: int = 0
    skipped_known: int = 0
    skipped_seen: int = 0
    junk: int = 0
    failed: bool = False
    keys: list[str] = field(default_factory=list)


def _flags_for(obs: Observation) -> frozenset[CandidateFlag]:
    flags = set(flags_mod.classify_token(obs.name))
    if obs.resolved is not None:
        flags |= flags_mod.classify_resolution(
            token_name=obs.name,
            token_e_number=obs.e_number,
            resolved_name=obs.resolved.common_name,
            resolved_e_number=obs.resolved.e_number,
            resolved_identity_uncertain=bool(obs.resolved.identity_uncertain),
        )
    return frozenset(flags)


async def record_observations(
    db: AsyncSession, observations: list[Observation], *, now: datetime | None = None
) -> ObservationSummary:
    """Counts each distinct not-yet-known token once for this request.
    Never raises."""
    summary = ObservationSummary()
    now = now or datetime.now(timezone.utc)
    seen: set[str] = db.info.setdefault(_SEEN_KEY, set())

    pending: dict[str, dict[str, Any]] = {}
    for obs in observations:
        if is_known_identity(obs.resolved):
            summary.skipped_known += 1
            continue
        key = flags_mod.candidate_key(obs.name)
        if not key:
            continue
        display, _ = flags_mod.bounded_display_name(obs.name)
        flags = set(_flags_for(obs))
        if key in pending:
            pending[key]["flags"] |= flags
            if pending[key]["ingredient_id"] is None and obs.resolved is not None:
                pending[key]["ingredient_id"] = obs.resolved.id
            continue
        pending[key] = {
            "display": display,
            "e_number": (obs.e_number or "").upper()[:16] or None,
            "ingredient_id": obs.resolved.id if obs.resolved is not None else None,
            "flags": flags,
        }

    todo = [key for key in sorted(pending) if key not in seen]  # sorted: stable lock order, no deadlocks
    summary.skipped_seen = len(pending) - len(todo)
    if not todo:
        return summary

    try:
        async with db.begin_nested():
            for key in todo:
                item = pending[key]
                outcome = await repo.observe(
                    db,
                    key=key,
                    display_name=item["display"],
                    e_number=item["e_number"],
                    ingredient_id=item["ingredient_id"],
                    flags=flags_mod.serialize_flags(item["flags"]),
                    status=status_for(item["flags"]),
                    now=now,
                    max_rows=settings.INGREDIENT_CANDIDATE_MAX_ROWS,
                    merge_flags=merge_flag_strings,
                )
                summary.inserted += outcome.inserted
                summary.updated += outcome.updated
                if outcome.dropped:
                    summary.dropped += 1
                if flags_mod.is_junk(item["flags"]):
                    summary.junk += 1
                summary.keys.append(key)
                seen.add(key)
    except Exception:  # noqa: BLE001 -- best effort by design; never fail the scan
        summary.failed = True
        seen.difference_update(todo)  # the SAVEPOINT rolled these back; a retry may count them
        _logger.warning("ingredient_candidate_queue_write_failed", tokens=len(todo))
        return summary

    if summary.dropped:
        _logger.warning(
            "ingredient_candidate_queue_full",
            dropped=summary.dropped,
            max_rows=settings.INGREDIENT_CANDIDATE_MAX_ROWS,
        )
    return summary


# ---------------------------------------------------------------------------
# Manual maintenance (dry-run by default; see app.seed.ingredient_candidate_maintenance)
# ---------------------------------------------------------------------------


@dataclass
class PrunePlan:
    candidates: list[tuple[int, str, str, int]]  # (id, status, flags, encounter_count) -- no text
    removed: int = 0
    dry_run: bool = True


async def prune(
    db: AsyncSession, *, dry_run: bool = True, now: datetime | None = None, limit: int = 1000
) -> PrunePlan:
    """Removes (or, by default, only lists) stale, rarely seen, unflagged
    queue rows. Touches only `ingredient_candidates`: never an
    `ingredients` row, an alias, or a product reference. Does not commit."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.INGREDIENT_CANDIDATE_RETENTION_DAYS)
    rows = await repo.list_prunable(
        db,
        unseen_since=cutoff,
        max_encounters=settings.INGREDIENT_CANDIDATE_PRUNE_MAX_ENCOUNTERS,
        limit=limit,
    )
    plan = PrunePlan(
        candidates=[(r.id, r.status, r.flags, r.encounter_count) for r in rows], dry_run=dry_run
    )
    if not dry_run:
        plan.removed = await repo.delete_by_ids(db, [r.id for r in rows])
    return plan


@dataclass
class BackfillPlan:
    would_create: int = 0
    created: int = 0
    already_present: int = 0
    dry_run: bool = True


async def backfill_from_catalog(db: AsyncSession, *, dry_run: bool = True) -> BackfillPlan:
    """Seeds queue rows for EXISTING uncurated catalog identities (OCR/
    Gemini-sourced, not trusted), so the queue is usable on data scanned
    before it existed. `encounter_count` is the MEASURED number of
    products referencing the identity (at least 1) and both timestamps are
    the row's own `retrieved_at`: they are lower bounds, not history.
    Never modifies `ingredients`, aliases or products. Does not commit."""
    plan = BackfillPlan(dry_run=dry_run)
    identities = (
        await db.execute(
            select(Ingredient).where(
                Ingredient.source.in_([IngredientSource.OCR_HEURISTIC, IngredientSource.GEMINI]),
                Ingredient.verification_status == IngredientVerificationStatus.UNVERIFIED,
            )
        )
    ).scalars().all()

    references: dict[str, int] = {}
    for (ids,) in (await db.execute(select(Product.ingredient_ids))).all():
        for ingredient_id in {i.strip() for i in (ids or "").split(",") if i.strip()}:
            references[ingredient_id] = references.get(ingredient_id, 0) + 1

    for ingredient in sorted(identities, key=lambda i: i.id):
        key = flags_mod.candidate_key(ingredient.common_name)
        if not key or await repo.get_by_key(db, key) is not None:
            plan.already_present += 1
            continue
        flags = set(flags_mod.classify_token(ingredient.common_name))
        if ingredient.identity_uncertain:
            flags.add(CandidateFlag.IDENTITY_UNCERTAIN)
        display, _ = flags_mod.bounded_display_name(ingredient.common_name)
        plan.would_create += 1
        if dry_run:
            continue
        seen_at = ingredient.retrieved_at or datetime.now(timezone.utc)
        outcome = await repo.observe(
            db,
            key=key,
            display_name=display,
            e_number=(ingredient.e_number or "").upper()[:16] or None,
            ingredient_id=ingredient.id,
            flags=flags_mod.serialize_flags(flags),
            status=status_for(flags),
            now=seen_at,
            max_rows=settings.INGREDIENT_CANDIDATE_MAX_ROWS,
            merge_flags=merge_flag_strings,
        )
        if outcome.inserted:
            plan.created += 1
            row = await repo.get_by_key(db, key)
            row.encounter_count = max(1, references.get(ingredient.id, 0))
    if not dry_run:
        await db.flush()
    return plan
