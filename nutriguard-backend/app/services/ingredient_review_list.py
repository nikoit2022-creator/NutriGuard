"""Read-only EN/BG owner view of the existing candidate observations.

Never infer identity from translated/similar names, and never promote evidence.
Original observations stay internal; names of unknown language are not exported.
"""
from collections import defaultdict
from datetime import timezone

from sqlalchemy import select

from app.models.enums import TRUSTED_INGREDIENT_SOURCES
from app.models.ingredient import Ingredient
from app.models.ingredient_alias import IngredientAlias
from app.models.ingredient_candidate import IngredientCandidate
from app.services.ingredient_candidate_flags import classify_token
from app.services.ingredient_localization import build_localizations
from app.services.ingredient_normalization import normalize_ingredient_name


def _name(value):
    text = " ".join((value or "").split())
    if not text or len(text) > 255 or classify_token(text):
        return None
    if text.casefold().startswith("synth_"):
        return None
    return text


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def assemble_review_list(candidates, ingredients, aliases):
    """One entry per catalog id; unresolved observations remain separate ids.

    Counts are token observations, NOT distinct requests across aliases. Identical
    names attached to different catalog ids are flagged, never silently merged.
    """
    by_id = {i.id: i for i in ingredients}
    aliases_by_id = defaultdict(list)
    for alias in aliases:
        if alias.language in ("en", "bg"):
            aliases_by_id[alias.ingredient_id].append(alias)
    groups = defaultdict(list)
    junk = 0
    for candidate in candidates:
        if candidate.status == "JUNK":
            junk += 1
            continue
        key = ("ingredient", candidate.ingredient_id) if candidate.ingredient_id in by_id else ("observation", candidate.id)
        groups[key].append(candidate)
    entries = []
    for (kind, key), observations in sorted(groups.items(), key=lambda pair: str(pair[0])):
        ingredient = by_id.get(key) if kind == "ingredient" else None
        names = {"en": None, "bg": None}
        evidence = {}
        if ingredient is not None:
            # Explicit language-tagged aliases (including validated machine
            # translations) are identity data, not scientific verification.
            for alias in sorted(aliases_by_id[key], key=lambda a: a.alias_normalized):
                value = _name(alias.alias_text)
                if value and names[alias.language] is None:
                    names[alias.language] = value
                    evidence[alias.language] = "LANGUAGE_TAGGED_ALIAS"
            canonical = _name(ingredient.common_name)
            # The paragraph-oriented language detector cannot certify short
            # names or mixed-language tokens. Untagged OCR text stays unnamed.
            if canonical and getattr(ingredient, "source", None) in TRUSTED_INGREDIENT_SOURCES:
                names["en"] = canonical
                evidence["en"] = "CURATED_CANONICAL_NAME"
            reviewed_bg = build_localizations(ingredient).get("bg", {}).get("commonName")
            if _name(reviewed_bg):
                names["bg"] = _name(reviewed_bg)
                evidence["bg"] = "CURRENT_REVIEWED_LOCALIZATION"
        flags = set()
        for observation in observations:
            flags.update(filter(None, (observation.flags or "").split(",")))
        if not any(names.values()):
            flags.add("NAME_LANGUAGE_REVIEW_REQUIRED")
        if ingredient is None:
            flags.add("IDENTITY_REVIEW_REQUIRED")
        entries.append({
            "reviewKey": f"{kind}:{key}",
            "ingredientId": ingredient.id if ingredient else None,
            "candidateIds": sorted(o.id for o in observations),
            "nameEn": names["en"], "nameBg": names["bg"],
            "nameEvidence": evidence,
            "eNumber": ingredient.e_number if ingredient else None,
            "firstSeenAt": min(_utc(o.first_seen_at) for o in observations).isoformat(),
            "lastSeenAt": max(_utc(o.last_seen_at) for o in observations).isoformat(),
            "tokenObservationCount": sum(o.encounter_count for o in observations),
            "flags": sorted(flags),
        })
    owners = defaultdict(set)
    for entry in entries:
        for field in ("nameEn", "nameBg"):
            if entry[field]:
                owners[(field, normalize_ingredient_name(entry[field]))].add(entry["reviewKey"])
    for entry in entries:
        if any(entry[field] and len(owners[(field, normalize_ingredient_name(entry[field]))]) > 1
               for field in ("nameEn", "nameBg")):
            entry["flags"] = sorted(set(entry["flags"]) | {"NAME_COLLISION_REVIEW_REQUIRED"})
    return {"schemaVersion": 1, "entryCount": len(entries), "junkObservationRowsExcluded": junk,
            "entries": entries}


async def build_review_list(db):
    """Read an existing queue without backfilling, repairing, or committing."""
    with db.no_autoflush:
        candidates = (await db.execute(select(IngredientCandidate).order_by(IngredientCandidate.id))).scalars().all()
        ids = sorted({row.ingredient_id for row in candidates if row.ingredient_id})
        ingredients, aliases = [], []
        # Bounded IN clauses also work on SQLite and large manually backfilled queues.
        for offset in range(0, len(ids), 400):
            batch = ids[offset:offset + 400]
            ingredients.extend((await db.execute(select(Ingredient).where(Ingredient.id.in_(batch)))).scalars().all())
            aliases.extend((await db.execute(select(IngredientAlias).where(IngredientAlias.ingredient_id.in_(batch)))).scalars().all())
        return assemble_review_list(candidates, ingredients, aliases)
