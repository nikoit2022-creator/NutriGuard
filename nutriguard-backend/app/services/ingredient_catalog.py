"""
The persistent ingredient knowledge cache: local-first canonical-
identity resolution for an OCR/Gemini-recognized ingredient with no
curated-database match, backed by the SAME `ingredients` table used for
curated/seeded data (see `app/models/ingredient.py`'s class docstring)
plus its alias index (`app/models/ingredient_alias.py`).

Design summary (task: "persistent ingredient knowledge cache"):
  1. Local-first (requirement 1): `get_or_create_catalog_ingredient`
     always tries an official identifier, then a known alias, before
     ever creating anything new.
  2. Canonical identity (requirement 2): official identifier > alias >
     normalized name -- see `get_or_create_catalog_ingredient`.
  3. Provenance (requirement 3): every row/alias carries `source`,
     `source_record_id`/`source_url`, `retrieved_at`, `last_verified_at`,
     `confidence`, `verification_status`, `schema_version` (see
     `app/models/ingredient.py`).
  4. Unknown ingredients (requirement 4): `_build_minimal_row` never
     fills a scientific/regulatory field -- it only ever receives an
     already-empty `SyntheticIngredient` (see `ocr_normalizer`'s own
     data-quality guarantees), so there's nothing to accidentally copy.
  5. Regulatory/scientific cache + TTL (requirement 5): `is_stale`/
     `is_within_negative_cache_window`/`merge_verified_fields`.
  6. Product relationships (requirement 6): unchanged by this module --
     `Product.ingredient_ids` already stores ids only, and
     `food_analysis.fetch_ingredients_for_product` already re-reads live
     `Ingredient` rows on every request. This module just makes sure a
     given normalized name/identifier always resolves to the SAME id.
  7. ADI (requirement 7): unaffected -- `IngredientOut`'s
     `adiMinMgPerKgBwPerDay`/`adiMaxMgPerKgBwPerDay` are derived at
     presentation time from the canonical row's own `acceptable_daily_intake`
     text (see `app.services.ingredient_regulatory`), never stored
     per-product.
  8. Privacy (requirement 8): nothing here ever touches an image, a
     full model prompt/response, a user id, or a health profile -- only
     ingredient name/identifier text and the provenance metadata above.
  9. Concurrency (requirement 9): `get_or_create_catalog_ingredient`
     uses the exact SAVEPOINT get-or-create pattern already established
     by `product_repository.insert_new`/`product_source_repository.record_discovery`
     (see `ingredient_repository.insert_new`/`ingredient_alias_repository.get_or_create`).

No external ingredient-lookup API exists in this codebase today (Gemini
is used only for whole-label extraction, never per-ingredient regulatory
lookup) -- `REGULATORY_LOOKUP`-sourced data, `LIMITED_DATA` status, and
`merge_verified_fields`'s confidence-gated overwrite protection are all
real, fully tested, ready seams for a FUTURE such integration, not
something faked here to justify testing them. See
`docs/CODEX_HANDOFF.md` for the full scoping note.
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.enums import IngredientSource, IngredientVerificationStatus, RiskLevel
from app.models.ingredient import Ingredient
from app.repositories import ingredient_alias_repository, ingredient_repository
from app.services.ingredient_normalization import normalize_ingredient_name
from app.services.ocr_normalizer import SyntheticIngredient

# Higher number = higher priority = harder to overwrite. A curated/
# seeded row is the ground truth; nothing OCR/Gemini ever observes may
# downgrade or overwrite it (task requirement 3: "Lower-quality OCR or
# Gemini data must never overwrite curated or regulatory information").
SOURCE_PRIORITY: dict[IngredientSource, int] = {
    IngredientSource.CURATED_SEED: 100,
    IngredientSource.REGULATORY_LOOKUP: 90,
    IngredientSource.GEMINI: 50,
    IngredientSource.OCR_HEURISTIC: 10,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite (this test suite's DB) doesn't preserve timezone info on
    a `DateTime(timezone=True)` column -- a value written as UTC comes
    back naive. Every timestamp this module ever writes IS UTC (see
    `_utcnow`), so treating a naive read-back as UTC is always correct,
    never a guess. Same pattern as `auth_service`'s
    `expires_at.replace(tzinfo=timezone.utc)`."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def derive_ins_number_from_e_number(e_number: str | None) -> str | None:
    """INS (Codex Alimentarius International Numbering System) code
    from an already-verified E-number. For the food additives these two
    schemes share, the EU E-number is built directly on the INS number
    (e.g. INS 951 == E951 aspartame) -- a safe, mechanical derivation
    from a genuine identifier, never a fabricated one. `None` for
    anything that isn't a plain "E" + digits (+ optional letter suffix)
    E-number."""
    if not e_number or not e_number.upper().startswith("E"):
        return None
    return e_number[1:].upper() or None


def is_stale(ingredient: Ingredient, *, now: datetime | None = None) -> bool:
    """Task requirement 5: a VERIFIED record's regulatory/scientific
    data is not cached forever. `True` once `last_verified_at` is older
    than `INGREDIENT_VERIFIED_DATA_TTL_SECONDS` -- the caller (see
    `IngredientOut.needs_refresh`) still serves the last known value
    immediately; staleness only flags it for a future refresh, it never
    blocks a scan. A row that was never verified at all (no
    `last_verified_at`) is not "stale" in this sense -- it's simply not
    verified, which `risk_assessment_available`/`verification_status`
    already communicate."""
    if ingredient.verification_status != IngredientVerificationStatus.VERIFIED:
        return False
    if ingredient.last_verified_at is None:
        return False
    now = now or _utcnow()
    age = now - _as_utc(ingredient.last_verified_at)
    return age > timedelta(seconds=settings.INGREDIENT_VERIFIED_DATA_TTL_SECONDS)


def is_within_negative_cache_window(ingredient: Ingredient, *, now: datetime | None = None) -> bool:
    """Task requirement 5: a failed/empty lookup may use a short
    negative-cache TTL so the same unresolved ingredient isn't
    re-attempted for every product. `True` while a not-yet-VERIFIED
    row's `retrieved_at` is within `INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS`
    -- the gate a future external-lookup/revalidation step should check
    before spending a real network call on a record this fresh (see the
    module docstring: no such external call exists in this codebase
    today, but `get_or_create_catalog_ingredient`'s local-first
    resolution already means an UNVERIFIED row is reused as-is either
    way -- this predicate is what a future revalidation attempt would
    gate on)."""
    if ingredient.verification_status == IngredientVerificationStatus.VERIFIED:
        return False
    if ingredient.retrieved_at is None:
        return False
    now = now or _utcnow()
    age = now - _as_utc(ingredient.retrieved_at)
    return age <= timedelta(seconds=settings.INGREDIENT_NEGATIVE_CACHE_TTL_SECONDS)


# Fields `merge_verified_fields` is allowed to touch -- deliberately
# only the scientific/regulatory ones a future regulatory-lookup
# integration would supply; identity columns (`id`, `e_number`, ...)
# and personalization-adjacent booleans (`bad_for_*`) are out of scope
# for this generic merge (see `_fill_missing_identity_fields` for the
# identity-only case this module actually exercises today).
_MERGEABLE_FIELDS = (
    "scientific_name", "category", "description", "purpose_in_food",
    "health_concerns", "evidence_level", "countries_restricted_or_banned",
    "efsa_status", "fda_status", "who_iarc_classification",
    "acceptable_daily_intake", "side_effects", "references", "risk_level",
)


def merge_verified_fields(
    existing: Ingredient,
    *,
    fields: dict[str, Any],
    source: IngredientSource,
    confidence: float,
) -> bool:
    """Apply `fields` (a subset of `_MERGEABLE_FIELDS`) onto `existing`
    ONLY if `source`/`confidence` outrank what's already stored --
    task requirement 3/5: lower-quality OCR/Gemini data must never
    overwrite curated or regulatory information. Returns whether
    anything was actually applied.

    Priority is source-rank first (`SOURCE_PRIORITY`), `confidence` as
    the tie-breaker within the same source rank -- a same-source
    resupply with a HIGHER confidence than what's stored may refresh
    it (e.g. a regulatory lookup revalidating its own earlier, lower-
    confidence answer), but nothing may ever cross a higher rank
    downward, regardless of confidence.
    """
    incoming_rank = SOURCE_PRIORITY[source]
    existing_rank = SOURCE_PRIORITY[existing.source]
    if incoming_rank < existing_rank:
        return False
    if incoming_rank == existing_rank and confidence <= float(existing.confidence):
        return False

    changed = False
    for field_name, value in fields.items():
        if field_name not in _MERGEABLE_FIELDS:
            continue
        if getattr(existing, field_name) != value:
            setattr(existing, field_name, value)
            changed = True
    if not changed:
        return False

    existing.source = source
    existing.confidence = confidence
    existing.retrieved_at = _utcnow()
    # Verification promotion is deliberately conservative: an AI-
    # generated (GEMINI) claim is real data worth storing -- outranking
    # a bare OCR guess -- but it is NOT a human/regulatory confirmation.
    # Only a REGULATORY_LOOKUP (or CURATED_SEED) source may promote a
    # row all the way to VERIFIED and set `risk_assessment_available`,
    # which is what actually lets `riskLevel` start influencing the
    # Health Score (see `food_analysis._score_and_warnings`) -- letting
    # a GEMINI-sourced merge do that would silently let an AI-suggested
    # risk assessment move the score, exactly what the data-quality task
    # (commit 1d8c3d9) exists to prevent. A GEMINI-sourced merge instead
    # promotes only to LIMITED_DATA -- real content, not yet confirmed.
    if SOURCE_PRIORITY[source] >= SOURCE_PRIORITY[IngredientSource.REGULATORY_LOOKUP]:
        existing.verification_status = IngredientVerificationStatus.VERIFIED
        existing.last_verified_at = _utcnow()
        existing.risk_assessment_available = True
    elif source == IngredientSource.GEMINI:
        existing.verification_status = IngredientVerificationStatus.LIMITED_DATA
    return True


def _fill_missing_identity_fields(existing: Ingredient, synthetic: SyntheticIngredient) -> None:
    """A later scan of an already-cached UNVERIFIED ingredient may
    recognize an official identifier the earlier observation missed
    (e.g. the E-number segment of the label was unreadable the first
    time). Only ever FILLS a currently-null identity field -- never
    overwrites one that's already set, so this can't downgrade or
    contradict an existing value, curated or not."""
    if existing.e_number is None and synthetic.e_number:
        existing.e_number = synthetic.e_number
        ins = derive_ins_number_from_e_number(synthetic.e_number)
        if existing.ins_number is None and ins:
            existing.ins_number = ins


def _build_minimal_row(synthetic: SyntheticIngredient, normalized: str) -> Ingredient:
    """Task requirement 4: a minimal observation record for future
    recognition -- normalized name, its identifier(s) when the OCR text
    genuinely contained one, and provenance. NOTHING else: `synthetic`
    is already guaranteed to carry no fabricated scientific/regulatory
    claim (see `ocr_normalizer.create_synthetic_ingredient`), so this
    function has nothing to filter -- it would be a bug in THAT
    function, not this one, if it ever did.
    """
    now = _utcnow()
    return Ingredient(
        id=synthetic.id,
        common_name=synthetic.common_name,
        normalized_name=normalized,
        scientific_name=synthetic.scientific_name,
        e_number=synthetic.e_number,
        ins_number=derive_ins_number_from_e_number(synthetic.e_number),
        category=synthetic.category,
        description=synthetic.description,
        purpose_in_food=synthetic.purpose_in_food,
        health_concerns=synthetic.health_concerns,
        evidence_level=synthetic.evidence_level,
        countries_restricted_or_banned=synthetic.countries_restricted_or_banned,
        efsa_status=synthetic.efsa_status,
        fda_status=synthetic.fda_status,
        who_iarc_classification=synthetic.who_iarc_classification,
        acceptable_daily_intake=synthetic.acceptable_daily_intake,
        side_effects=synthetic.side_effects,
        allergens=synthetic.allergens,
        references=synthetic.references,
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        verification_status=IngredientVerificationStatus.UNVERIFIED,
        source=IngredientSource.OCR_HEURISTIC,
        source_record_id=None,
        source_url=None,
        retrieved_at=now,
        last_verified_at=None,
        confidence=0.2,
        schema_version=1,
        is_gluten=synthetic.is_gluten,
        is_lactose=synthetic.is_lactose,
        is_vegan=synthetic.is_vegan,
        is_vegetarian=synthetic.is_vegetarian,
        is_halal=synthetic.is_halal,
        is_kosher=synthetic.is_kosher,
        bad_for_diabetes=synthetic.bad_for_diabetes,
        bad_for_hypertension=synthetic.bad_for_hypertension,
        bad_for_kidney_disease=synthetic.bad_for_kidney_disease,
        bad_for_gout=synthetic.bad_for_gout,
        bad_for_pregnancy=synthetic.bad_for_pregnancy,
        bad_for_children=synthetic.bad_for_children,
        bad_for_high_cholesterol=synthetic.bad_for_high_cholesterol,
    )


async def get_or_create_catalog_ingredient(db: AsyncSession, synthetic: SyntheticIngredient) -> Ingredient:
    """Local-first canonical-identity resolution for one OCR-recognized
    token with no curated-database match (task requirements 1 + 2):

      1. Official identifier (E-number here; INS/CAS supported by the
         lookup itself once a future source populates them) -- the
         strongest identity key.
      2. A known alias -- any previously-learned English/Bulgarian/
         spelling/OCR-variant name pointing at a canonical ingredient.
      3. Otherwise: get-or-create a minimal UNVERIFIED row, race-safe
         (requirement 9), and register this name as its first alias so
         the NEXT occurrence of the same normalized text -- from this
         request or a concurrent one -- resolves via step 2 instead of
         creating another row.

    Never treats the deterministic `synth_...` id/hash itself as proof
    of identity (requirement 2) -- it is only ever used as a primary
    key to look an ALREADY-established row back up (step 3's own
    get-or-create), never as the reason two tokens are considered the
    same ingredient; that judgment is always the identifier/alias/
    normalized-name resolution above.
    """
    normalized = normalize_ingredient_name(synthetic.common_name)
    resolved: Ingredient | None = None

    if synthetic.e_number:
        resolved = await ingredient_repository.get_by_official_identifier(db, e_number=synthetic.e_number)

    if resolved is None:
        alias = await ingredient_alias_repository.get_by_normalized(db, normalized)
        if alias is not None:
            resolved = await ingredient_repository.get_by_id(db, alias.ingredient_id)
            if resolved is not None:
                # This exact normalized text already has an alias --
                # nothing new to register below, return immediately.
                _fill_missing_identity_fields(resolved, synthetic)
                return resolved

    if resolved is not None:
        # Resolved via E-number (step 1), but THIS specific display text
        # has no alias of its own yet -- register it too, so a later
        # scan of the same name that DOESN'T also catch the E-number
        # (e.g. a blurrier crop) still resolves directly via alias
        # instead of needing another E-number-only lookup.
        _fill_missing_identity_fields(resolved, synthetic)
        await ingredient_alias_repository.get_or_create(
            db,
            ingredient_id=resolved.id,
            alias_text=synthetic.common_name,
            alias_normalized=normalized,
            language=None,
            source=IngredientSource.OCR_HEURISTIC,
        )
        return resolved

    # Genuinely new -- race-safe get-or-create. Two concurrent scans of
    # the same never-before-seen ingredient normally compute the SAME
    # deterministic id (see `ocr_normalizer.create_synthetic_ingredient`)
    # and the same `normalized` text, so whichever one's INSERT commits
    # first wins and the loser's `insert_new` returns `None` (a plain
    # primary-key conflict on `id`) -- but two DIFFERENT display names
    # sharing the same genuine E-number (e.g. "Vitamin C (E300)" vs.
    # "Ascorbic Acid (E300)") produce DIFFERENT ids/normalized names and
    # instead conflict on the UNIQUE `e_number` column -- `id` is not
    # the only identity `insert_new` can lose a race on, so re-fetching
    # by id/alias alone is not enough; also re-check every official
    # identifier this row carries before concluding the conflict is
    # unrecoverable.
    row = _build_minimal_row(synthetic, normalized)
    inserted = await ingredient_repository.insert_new(db, row)
    if inserted is None:
        inserted = await ingredient_repository.get_by_id(db, synthetic.id)
    if inserted is None:
        alias = await ingredient_alias_repository.get_by_normalized(db, normalized)
        if alias is not None:
            inserted = await ingredient_repository.get_by_id(db, alias.ingredient_id)
    if inserted is None and (synthetic.e_number or row.ins_number or row.cas_number):
        inserted = await ingredient_repository.get_by_official_identifier(
            db, e_number=synthetic.e_number, ins_number=row.ins_number, cas_number=row.cas_number
        )
    if inserted is None:
        raise RuntimeError(
            f"Ingredient insert for id={synthetic.id!r} (e_number={synthetic.e_number!r}) "
            "conflicted but no row could be re-fetched by id, alias, or official identifier "
            "-- this should be unreachable under the documented SAVEPOINT get-or-create "
            "guarantee (see product_repository.insert_new)."
        )
    else:
        _fill_missing_identity_fields(inserted, synthetic)

    await ingredient_alias_repository.get_or_create(
        db,
        ingredient_id=inserted.id,
        alias_text=synthetic.common_name,
        alias_normalized=normalized,
        language=None,
        source=IngredientSource.OCR_HEURISTIC,
    )
    return inserted


async def materialize_ingredients(db: AsyncSession, ingredients: list[Any]) -> list[Any]:
    """Replace every in-memory-only `SyntheticIngredient` in `ingredients`
    with its persisted catalog row (get-or-create, race-safe) -- called
    once, right after ingredient matching, at each of `food_analysis`'s
    scan pipelines. A curated `Ingredient` row already returned by
    `ocr_normalizer.match_against_database` passes through unchanged.
    """
    materialized: list[Any] = []
    for ing in ingredients:
        if isinstance(ing, SyntheticIngredient):
            materialized.append(await get_or_create_catalog_ingredient(db, ing))
        else:
            materialized.append(ing)
    return materialized
