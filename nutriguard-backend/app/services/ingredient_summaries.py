"""
Issue #23 (stage 3): stored, source-backed ingredient summaries.

A summary is a small, sectioned document (ORIGIN, FUNCTION, EFFECTS,
JURISDICTION; any section absent when nothing verified supports it) with
citations kept at the bottom and per-language text. It is written by the
loader from committed, reviewed content; nothing here calls a model or an
external service, and nothing is generated per scan.

Rules enforced here (each covered by tests):

  * Exact subject only. A summary attaches to an ingredient by its official
    E-number, or by its exact normalized name. No fuzzy, stemmed or
    translated match. Identity uncertainty only narrows the match (see
    `candidate_subject_keys`).
  * A generic E-number summary is served BESIDE the ingredient's own fields
    (`IngredientOut.summary`) and never merged into or over them.
  * English is served only when `evidence_state == SOURCE_VERIFIED`.
    Bulgarian only when `REVIEWED` and its `source_content_hash` equals the
    summary's current `content_hash`. Anything else is not served and the
    client falls back to the English sections.
  * Empty sections are absent, never filler.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization
from app.services.ingredient_normalization import normalize_ingredient_name

SECTION_KINDS = ("ORIGIN", "FUNCTION", "EFFECTS", "JURISDICTION")
ACCESS_TYPES = ("FULL_TEXT", "ABSTRACT", "OFFICIAL_PAGE")
SCOPES = ("E_NUMBER_GENERIC", "INGREDIENT_NAME")
NAME_PREFIX = "name:"


def content_hash(sections: list[dict], citations: list[dict]) -> str:
    """Fingerprint of the English content a translation is a translation of."""
    payload = {"sections": sections, "citations": citations}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def subject_key_for_e_number(e_number: str) -> str:
    return e_number.strip().upper().replace(" ", "")


def subject_key_for_name(name: str) -> str:
    return NAME_PREFIX + normalize_ingredient_name(name)


def candidate_subject_keys(ingredient: Any) -> list[str]:
    """Subjects an ingredient may exactly correspond to, most specific
    first: its official E-number, then its exact normalized name.

    Identity uncertainty narrows this, never widens it:

      * not uncertain: E-number, then exact name;
      * uncertain only because the NAME's language/translation could not be
        verified (`TRANSLATION_UNRELIABLE`): the E-number alone. The code is
        extracted deterministically and is the catalog's own definitive
        identifier (see `ingredient_catalog`), so a family summary keyed on
        it does not depend on the unverified name. A name-scoped summary
        would, so it is withheld;
      * uncertain because the token itself may be several merged clauses
        (colon merge, duplicated fragment) or for any other reason: none.
    """
    keys: list[str] = []
    e_number = (getattr(ingredient, "e_number", None) or "").strip()
    if getattr(ingredient, "identity_uncertain", False):
        if getattr(ingredient, "uncertainty_reason", None) == "TRANSLATION_UNRELIABLE" and e_number:
            return [subject_key_for_e_number(e_number)]
        return []
    if e_number:
        keys.append(subject_key_for_e_number(e_number))
    name = normalize_ingredient_name(getattr(ingredient, "common_name", "") or "")
    if name:
        keys.append(NAME_PREFIX + name)
    return keys


def validate_subject(subject: dict) -> list[str]:
    """Structural problems with one subject of the content file (empty when
    fine). Used by the loader, which refuses a subject with any problem."""
    problems: list[str] = []
    key = subject.get("subjectKey", "")
    if subject.get("scope") not in SCOPES:
        problems.append(f"{key}: unknown scope")
    if (subject.get("scope") == "E_NUMBER_GENERIC") != (not key.startswith(NAME_PREFIX)):
        problems.append(f"{key}: scope does not match the key form")
    citations = subject.get("citations", [])
    ids = [c.get("id") for c in citations]
    if len(ids) != len(set(ids)):
        problems.append(f"{key}: duplicate citation ids")
    for c in citations:
        for field in ("id", "label", "url", "documentDate", "accessType"):
            if not c.get(field):
                problems.append(f"{key}: citation {c.get('id')!r} lacks {field}")
        if c.get("accessType") not in ACCESS_TYPES:
            problems.append(f"{key}: citation {c.get('id')!r} has an unknown accessType")
        if not str(c.get("url", "")).startswith("https://"):
            problems.append(f"{key}: citation {c.get('id')!r} url is not https")
    sections = subject.get("sections", [])
    if not sections:
        problems.append(f"{key}: no sections")
    order = {k: i for i, k in enumerate(SECTION_KINDS)}
    last = -1
    for sec in sections:
        kind = sec.get("kind")
        if kind not in SECTION_KINDS:
            problems.append(f"{key}: unknown section kind {kind!r}")
            continue
        if order[kind] < last:
            problems.append(f"{key}: sections out of order at {kind}")
        last = order[kind]
        if not (sec.get("text") or "").strip():
            problems.append(f"{key}: empty {kind} section (absent sections must be omitted)")
        if not sec.get("citationIds"):
            problems.append(f"{key}: {kind} section has no citation")
        for cid in sec.get("citationIds", []):
            if cid not in ids:
                problems.append(f"{key}: {kind} cites unknown id {cid!r}")
        if (kind == "JURISDICTION") != bool(sec.get("jurisdiction")):
            problems.append(f"{key}: jurisdiction must be set on JURISDICTION sections only")
    for citation in citations:
        if not any(citation["id"] in sec.get("citationIds", []) for sec in sections):
            problems.append(f"{key}: citation {citation['id']!r} is not used by any section")
    for lang, loc in (subject.get("localizations") or {}).items():
        secs = loc.get("sections", [])
        if [(s.get("kind"), s.get("jurisdiction")) for s in secs] != [
            (s.get("kind"), s.get("jurisdiction")) for s in sections
        ]:
            problems.append(f"{key}: {lang} sections do not line up with the English ones")
        if any(not (s.get("text") or "").strip() for s in secs):
            problems.append(f"{key}: {lang} has an empty section")
    return problems


def _to_epoch_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def build_payload(row: IngredientSummary | None) -> dict | None:
    """The served `summary` object for one stored row, or None when it may
    not be served. Plain JSON-serializable data (camelCase wire shape)."""
    if row is None or row.evidence_state != "SOURCE_VERIFIED":
        return None
    sections = json.loads(row.sections_json)
    citations = {c["id"]: c for c in json.loads(row.citations_json)}
    if not sections:
        return None

    used: dict[str, set[str]] = {}
    for sec in sections:
        for cid in sec.get("citationIds", []):
            used.setdefault(cid, set()).add(sec["kind"])
    served_citations = [
        {
            "id": cid,
            "label": citations[cid]["label"],
            "url": citations[cid]["url"],
            "documentDate": citations[cid]["documentDate"],
            "accessType": citations[cid]["accessType"],
            "supports": [k for k in SECTION_KINDS if k in used[cid]],
        }
        for cid in citations
        if cid in used
    ]
    served_sections = []
    for sec in sections:
        item: dict[str, Any] = {"kind": sec["kind"], "text": sec["text"], "citationIds": list(sec["citationIds"])}
        if sec.get("jurisdiction"):
            item["jurisdiction"] = sec["jurisdiction"]
        served_sections.append(item)

    localizations: dict[str, Any] = {}
    for loc in row.localizations:
        if (
            loc.language == "bg"
            and loc.translation_status == "REVIEWED"
            and loc.source_content_hash == row.content_hash
        ):
            bg_sections = json.loads(loc.sections_json)
            if [(s["kind"], s.get("jurisdiction")) for s in bg_sections] == [
                (s["kind"], s.get("jurisdiction")) for s in sections
            ]:
                localizations["bg"] = {
                    "sections": [
                        {
                            "kind": s["kind"],
                            "text": s["text"],
                            **({"jurisdiction": s["jurisdiction"]} if s.get("jurisdiction") else {}),
                        }
                        for s in bg_sections
                    ],
                    "translationStatus": "REVIEWED",
                    "translationSource": loc.translation_source,
                }
    return {
        "scope": row.scope,
        "evidenceState": row.evidence_state,
        "humanReviewed": bool(row.human_reviewed),
        "verifiedAt": _to_epoch_ms(row.source_verified_at),
        "sections": served_sections,
        "citations": served_citations,
        "localizations": localizations,
    }


async def attach(db: AsyncSession, ingredients: list[Any]) -> None:
    """Sets `loaded_summary` on each ingredient that exactly corresponds to a
    stored, servable summary (and `None` on the rest). One query for the whole
    list. Never raises: a failure leaves responses exactly as they were."""
    if not settings.INGREDIENT_SUMMARIES_SERVE:
        return
    # A row already handled earlier in this request (e.g. by
    # `fetch_ingredients_for_product`) is not queried again.
    ingredients = [i for i in ingredients if not getattr(i, "_summary_attached", False)]
    if not ingredients:
        return
    keys_by_index = [candidate_subject_keys(ing) for ing in ingredients]
    wanted = {key for keys in keys_by_index for key in keys}
    if not wanted:
        return
    try:
        rows = (
            await db.execute(select(IngredientSummary).where(IngredientSummary.subject_key.in_(wanted)))
        ).scalars().all()
    except Exception:  # noqa: BLE001 -- summaries are optional enrichment
        return
    by_key = {row.subject_key: row for row in rows}
    for ingredient, keys in zip(ingredients, keys_by_index):
        payload = None
        for key in keys:  # most specific subject first
            payload = build_payload(by_key.get(key))
            if payload is not None:
                break
        try:
            ingredient.loaded_summary = payload
            ingredient._summary_attached = True
        except Exception:  # noqa: BLE001 -- a frozen/plain object simply gets none
            pass
