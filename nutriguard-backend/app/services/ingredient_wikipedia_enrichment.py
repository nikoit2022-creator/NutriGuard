"""
Wikipedia fallback for ingredient enrichment (task: "Add Wikipedia-API
as a fallback source for ingredient information").

Called once per already locally-resolved ingredient, from
`app.services.ingredient_catalog._enrich_from_wikipedia_if_needed`
(itself called from `materialize_ingredients`, right after
`get_or_create_catalog_ingredient`). Local-first by construction: this
module only ever runs AFTER the catalog's own identifier/alias/
normalized-name resolution has already happened, and only when that
resolution left the ingredient without a usable description
(`needs_enrichment` below).

Only a blank `description` is ever filled here, via the narrow
`ingredient_catalog.merge_wikipedia_description` helper. It keeps any
higher-ranked record source and verification status intact, and records
Wikipedia as the source of the description field. A cached successful
lookup is also applied on a later scan if an earlier rank gate left the
description empty. The REST summary
endpoint returns one short, unstructured extract; deriving a separate
"category"/"purpose_in_food"/"scientific_name" from that free text would
require inferring structure Wikipedia didn't give us, which is exactly
the kind of invented information the task's data-limitations section
forbids. `efsa_status`, `fda_status`, `who_iarc_classification`,
`acceptable_daily_intake`, `risk_level`, `health_concerns`,
`side_effects` are never touched -- `IngredientSource.WIKIPEDIA_API` is
deliberately absent from `TRUSTED_INGREDIENT_SOURCES`, so even a future
accidental attempt to merge one of those fields from this source could
never be presented as an authoritative regulatory/medical claim (see
`app.services.ingredient_regulatory.is_authoritative_regulatory_source`).

Provenance, caching, and the manual-review queue for an unclear name or
a failed/uncertain lookup all live in one place:
`app.models.ingredient_wikipedia_lookup.IngredientWikipediaLookup`, one
row per ingredient, written by
`app.repositories.ingredient_wikipedia_lookup_repository`.
"""
import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.wikipedia_api import WikipediaApiClient, WikipediaLookupError
from app.models.ingredient import Ingredient
from app.models.ingredient_wikipedia_lookup import IngredientWikipediaLookup
from app.repositories import ingredient_wikipedia_lookup_repository
from app.services import ingredient_catalog
from app.services.barcode_text_safety import is_placeholder
from app.services.ingredient_candidate_flags import CandidateFlag, classify_token

_logger = structlog.get_logger(__name__)

# The only `Ingredient` field this module ever writes -- see the module
# docstring for why the others are structurally out of scope.
_MERGE_FIELD = "description"

# Closed vocabulary: a name-level reason NOT to search Wikipedia at all
# (task rule 3: "Do not search using vague or generic names ... mark
# those records for review"). Reuses the SAME token classifier the OCR
# candidate queue already uses (`ingredient_candidate_flags`), so a name
# considered too vague to trust there is treated identically here.
_SKIP_NAME_FLAGS: dict[CandidateFlag, str] = {
    CandidateFlag.NO_LETTERS: "NO_LETTERS",
    CandidateFlag.PLACEHOLDER_TEXT: "PLACEHOLDER_TEXT",
    CandidateFlag.GENERIC_FUNCTION_TERM: "GENERIC_FUNCTION_TERM",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """Same SQLite-naive-datetime fix as `ingredient_catalog._as_utc`:
    a value written as UTC comes back naive on this test suite's
    SQLite DB, and everything this module ever writes IS UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _is_blank(value: str | None) -> bool:
    if value is None:
        return True
    return not value.strip() or is_placeholder(value)


def needs_enrichment(ingredient: Ingredient) -> bool:
    """Call Wikipedia when local sources left the ingredient without a
    usable description, regardless of its record-level verification
    status. `merge_verified_fields` only fills blank fields and preserves
    higher-priority curated/regulatory content, so this can safely
    supplement an otherwise verified row without replacing its evidence."""
    return _is_blank(ingredient.description)


def ineligible_name_reason(ingredient: Ingredient) -> str | None:
    """A closed-vocabulary reason to skip the Wikipedia call entirely
    and flag this ingredient for manual review instead of ever
    searching on a name that cannot be reliably identified (task rule
    3). `None` means the name is eligible to search."""
    if ingredient.identity_uncertain:
        return "IDENTITY_UNCERTAIN"
    flags = classify_token(ingredient.common_name)
    for flag, reason in _SKIP_NAME_FLAGS.items():
        if flag in flags:
            return reason
    return None


def _is_cache_fresh(lookup: IngredientWikipediaLookup | None, *, now: datetime) -> bool:
    """Task: "cache results to avoid unnecessary repeat requests."
    A MATCHED lookup is fresh for `WIKIPEDIA_LOOKUP_CACHE_TTL_SECONDS`;
    any other status (NOT_FOUND/AMBIGUOUS/ERROR/SKIPPED_UNCLEAR_NAME) is
    a negative-cache entry, fresh for the shorter
    `WIKIPEDIA_NEGATIVE_CACHE_TTL_SECONDS` -- same two-tier pattern as
    `ingredient_catalog.is_stale`/`is_within_negative_cache_window`."""
    if lookup is None:
        return False
    if lookup.match_status == "MATCHED":
        if lookup.retrieved_at is None:
            return False
        age = now - _as_utc(lookup.retrieved_at)
        return age <= timedelta(seconds=settings.WIKIPEDIA_LOOKUP_CACHE_TTL_SECONDS)
    age = now - _as_utc(lookup.attempted_at)
    return age <= timedelta(seconds=settings.WIKIPEDIA_NEGATIVE_CACHE_TTL_SECONDS)


async def enrich_ingredient_from_wikipedia(
    db: AsyncSession, ingredient: Ingredient, *, client: WikipediaApiClient | None = None
) -> None:
    """Fallback lookup for ONE already-locally-resolved ingredient.
    Never raises: every expected outcome (cache hit, unclear name, no
    page, ambiguous page, network/API error) is handled and recorded as
    provenance on `IngredientWikipediaLookup` rather than propagated --
    task: "a temporary Wikipedia outage must not stop processing other
    ingredients." `client` is test-only (dependency injection for a
    pre-configured `WikipediaApiClient`); production callers never pass
    it.
    """
    if not settings.WIKIPEDIA_LOOKUP_ENABLED:
        return
    if not needs_enrichment(ingredient):
        return

    now = _utcnow()
    existing = await ingredient_wikipedia_lookup_repository.get_by_ingredient_id(db, ingredient.id)
    if _is_cache_fresh(existing, now=now):
        if (
            existing is not None
            and existing.match_status == "MATCHED"
            and existing.extracted_summary
            and _is_blank(ingredient.description)
        ):
            retrieved_at = _as_utc(existing.retrieved_at) if existing.retrieved_at else now
            changed = ingredient_catalog.merge_wikipedia_description(
                ingredient,
                description=existing.extracted_summary,
                source_url=existing.source_url,
                confidence=float(existing.confidence),
                now=retrieved_at,
            )
            if changed:
                existing.fields_populated_json = json.dumps([_MERGE_FIELD])
                await db.flush()
        return

    query_text = ingredient.common_name
    skip_reason = ineligible_name_reason(ingredient)
    if skip_reason is not None:
        await ingredient_wikipedia_lookup_repository.record_lookup(
            db,
            ingredient_id=ingredient.id,
            query_text=query_text,
            match_status="SKIPPED_UNCLEAR_NAME",
            review_reason=skip_reason,
        )
        return

    active_client = client or WikipediaApiClient(
        base_url=settings.WIKIPEDIA_API_BASE_URL,
        user_agent=settings.WIKIPEDIA_USER_AGENT,
        timeout_seconds=settings.WIKIPEDIA_TIMEOUT_SECONDS,
        max_retries=settings.WIKIPEDIA_MAX_RETRIES,
    )

    try:
        summary = await active_client.fetch_summary(query_text)
    except WikipediaLookupError as exc:
        _logger.warning("wikipedia_lookup_failed", ingredient_id=ingredient.id, reason=str(exc))
        await ingredient_wikipedia_lookup_repository.record_lookup(
            db,
            ingredient_id=ingredient.id,
            query_text=query_text,
            match_status="ERROR",
            review_reason="API_ERROR",
            error_detail=str(exc)[:255],
        )
        return

    if summary is None:
        await ingredient_wikipedia_lookup_repository.record_lookup(
            db, ingredient_id=ingredient.id, query_text=query_text, match_status="NOT_FOUND",
        )
        return

    if summary.is_disambiguation:
        await ingredient_wikipedia_lookup_repository.record_lookup(
            db,
            ingredient_id=ingredient.id,
            query_text=query_text,
            match_status="AMBIGUOUS",
            review_reason="MULTIPLE_CANDIDATE_PAGES",
            matched_page_title=summary.title,
            source_url=summary.page_url,
        )
        return

    changed = ingredient_catalog.merge_wikipedia_description(
        ingredient,
        description=summary.extract,
        source_url=summary.page_url,
        confidence=settings.WIKIPEDIA_MERGE_CONFIDENCE,
        now=now,
    )
    await ingredient_wikipedia_lookup_repository.record_lookup(
        db,
        ingredient_id=ingredient.id,
        query_text=query_text,
        match_status="MATCHED",
        matched_page_title=summary.title,
        source_url=summary.page_url,
        extracted_summary=summary.extract,
        fields_populated_json=json.dumps([_MERGE_FIELD]) if changed else json.dumps([]),
        confidence=settings.WIKIPEDIA_MERGE_CONFIDENCE,
        retrieved_at=now,
    )
