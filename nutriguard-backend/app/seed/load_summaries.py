"""
Issue #23 (stage 3): idempotent loader for source-backed ingredient summaries.

Reads `app/seed/ingredient_summaries.json` (committed, reviewed content) and
upserts `ingredient_summaries` / `ingredient_summary_localizations`. It never
calls a model or an external service, and never runs per scan.

Safety rules (each covered by tests):

  * A subject with any structural problem (`validate_subject`) stops the
    whole load before anything is written: no partial, half-validated write.
  * Idempotent: a second run with the same file changes nothing.
  * Changed English content replaces the stored sections and resets
    `human_reviewed` (a person reviewed different text). It also makes every
    older translation stale: a stale translation is replaced by the file's
    new DRAFT, and is never served in the meantime (the served gate compares
    `source_content_hash` with the summary's `content_hash`).
  * A REVIEWED translation whose hash still matches the English content is
    left exactly as it is; the loader never downgrades or overwrites a
    person's review.
  * The loader NEVER writes `REVIEWED`. Machine-translated Bulgarian arrives
    as `DRAFT` / `MACHINE_TRANSLATED`. Approval is a separate, explicit,
    human action (`app.seed.summary_review approve`).

Usage:
    python -m app.seed.load_summaries
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.session import AsyncSessionLocal
from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization
from app.services.ingredient_summaries import content_hash, validate_subject

logger = structlog.get_logger(__name__)

SUMMARIES_SEED_FILE = Path(__file__).parent / "ingredient_summaries.json"
SUPPORTED_SCHEMA_VERSION = 1


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_sections(sections: list[dict]) -> list[dict]:
    out = []
    for sec in sections:
        item = {"kind": sec["kind"], "text": sec["text"], "citationIds": list(sec["citationIds"])}
        if sec.get("jurisdiction"):
            item["jurisdiction"] = sec["jurisdiction"]
        out.append(item)
    return out


def _canonical_localized_sections(sections: list[dict]) -> list[dict]:
    out = []
    for sec in sections:
        item = {"kind": sec["kind"], "text": sec["text"]}
        if sec.get("jurisdiction"):
            item["jurisdiction"] = sec["jurisdiction"]
        out.append(item)
    return out


def _canonical_citations(citations: list[dict]) -> list[dict]:
    return [
        {
            "id": c["id"],
            "label": c["label"],
            "url": c["url"],
            "documentDate": c["documentDate"],
            "accessType": c["accessType"],
        }
        for c in citations
    ]


def _parse_verified_at(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def read_seed_file(path: Path | None = None) -> list[dict]:
    """The validated subjects of the content file. Raises `ValueError` listing
    every problem when any subject is malformed, so nothing is half-loaded."""
    data = json.loads((path or SUMMARIES_SEED_FILE).read_text(encoding="utf-8"))
    if data.get("schemaVersion") != SUPPORTED_SCHEMA_VERSION:
        raise ValueError(f"unsupported summaries schemaVersion {data.get('schemaVersion')!r}")
    subjects = data.get("subjects", [])
    problems: list[str] = []
    keys = [s.get("subjectKey") for s in subjects]
    if len(keys) != len(set(keys)):
        problems.append("duplicate subjectKey in file")
    for subject in subjects:
        problems.extend(validate_subject(subject))
        if not subject.get("claims"):
            problems.append(f"{subject.get('subjectKey')}: no claims ledger")
    if problems:
        raise ValueError("ingredient summaries file rejected: " + "; ".join(problems))
    return subjects


async def _get_summary(session, subject_key: str) -> IngredientSummary | None:
    return (
        await session.execute(select(IngredientSummary).where(IngredientSummary.subject_key == subject_key))
    ).scalar_one_or_none()


async def load_summaries(session, path: Path | None = None) -> dict[str, int]:
    """Upserts every subject of the content file. Returns counters (created,
    updated, unchanged, translations_created/updated/kept)."""
    subjects = read_seed_file(path)
    counters = {
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "translations_created": 0,
        "translations_updated": 0,
        "translations_kept": 0,
    }
    now = datetime.now(timezone.utc)
    for subject in subjects:
        sections = _canonical_sections(subject["sections"])
        citations = _canonical_citations(subject["citations"])
        claims = subject["claims"]
        digest = content_hash(sections, citations)
        verified_at = _parse_verified_at(subject.get("verifiedAt"))

        row = await _get_summary(session, subject["subjectKey"])
        created = False
        if row is None:
            candidate = IngredientSummary(
                subject_key=subject["subjectKey"],
                scope=subject["scope"],
                evidence_state="SOURCE_VERIFIED",
                human_reviewed=False,
                sections_json=_json(sections),
                citations_json=_json(citations),
                claims_json=_json(claims),
                content_hash=digest,
                source_verified_at=verified_at,
                schema_version=1,
                created_at=now,
                updated_at=now,
            )
            try:
                # A savepoint, so a concurrent loader that inserted the same
                # subject first costs one re-read, not the whole transaction.
                async with session.begin_nested():
                    session.add(candidate)
                    await session.flush()
                row = candidate
                created = True
                counters["created"] += 1
            except IntegrityError:
                row = await _get_summary(session, subject["subjectKey"])
                if row is None:
                    raise
        if created:
            pass
        elif row.content_hash != digest:
            row.scope = subject["scope"]
            row.evidence_state = "SOURCE_VERIFIED"
            # A person reviewed different text: that review does not carry over.
            row.human_reviewed = False
            row.sections_json = _json(sections)
            row.citations_json = _json(citations)
            row.claims_json = _json(claims)
            row.content_hash = digest
            row.source_verified_at = verified_at
            row.updated_at = now
            counters["updated"] += 1
        else:
            # Same English content: only the internal ledger / check date may move.
            changed = False
            if row.claims_json != _json(claims):
                row.claims_json = _json(claims)
                changed = True
            if verified_at is not None and _as_utc(row.source_verified_at) != verified_at:
                row.source_verified_at = verified_at
                changed = True
            if changed:
                row.updated_at = now
                counters["updated"] += 1
            else:
                counters["unchanged"] += 1

        for language, localized in (subject.get("localizations") or {}).items():
            loc_sections = _canonical_localized_sections(localized["sections"])
            existing = await session.get(IngredientSummaryLocalization, (row.id, language))
            if existing is None:
                fresh = IngredientSummaryLocalization(
                    summary_id=row.id,
                    language=language,
                    sections_json=_json(loc_sections),
                    translation_status="DRAFT",
                    translation_source="MACHINE_TRANSLATED",
                    source_content_hash=digest,
                    created_at=now,
                    updated_at=now,
                )
                try:
                    async with session.begin_nested():
                        session.add(fresh)
                        await session.flush()
                    counters["translations_created"] += 1
                    continue
                except IntegrityError:
                    existing = await session.get(IngredientSummaryLocalization, (row.id, language))
                    if existing is None:
                        raise
            if existing.translation_status == "REVIEWED" and existing.source_content_hash == digest:
                counters["translations_kept"] += 1
            elif (
                existing.translation_status == "DRAFT"
                and existing.source_content_hash == digest
                and existing.sections_json == _json(loc_sections)
            ):
                counters["translations_kept"] += 1
            else:
                existing.sections_json = _json(loc_sections)
                existing.translation_status = "DRAFT"
                existing.translation_source = "MACHINE_TRANSLATED"
                existing.source_content_hash = digest
                existing.reviewed_at = None
                existing.reviewed_by = None
                existing.updated_at = now
                counters["translations_updated"] += 1
    await session.flush()
    return counters


async def main() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        counters = await load_summaries(session)
        await session.commit()
    logger.info("ingredient_summaries_loaded", **counters)
    return counters


if __name__ == "__main__":
    print(asyncio.run(main()))
