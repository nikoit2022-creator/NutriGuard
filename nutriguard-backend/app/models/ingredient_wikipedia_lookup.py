import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# Closed vocabulary (also enforced by the CHECK constraint below) --
# same convention as app.models.ingredient_candidate.CANDIDATE_STATUSES /
# app.services.ingredient_candidate_flags.CandidateFlag: a plain String
# column, not a native Postgres enum, so the vocabulary can grow later
# without an ALTER TYPE migration.
WIKIPEDIA_LOOKUP_STATUSES = ("MATCHED", "NOT_FOUND", "AMBIGUOUS", "SKIPPED_UNCLEAR_NAME", "ERROR")

# Closed vocabulary for `review_reason` -- set only on a status that
# needs a human to look (anything other than MATCHED/NOT_FOUND).
WIKIPEDIA_LOOKUP_REVIEW_REASONS = (
    "NO_LETTERS",
    "PLACEHOLDER_TEXT",
    "GENERIC_FUNCTION_TERM",
    "IDENTITY_UNCERTAIN",
    "MULTIPLE_CANDIDATE_PAGES",
    "API_ERROR",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngredientWikipediaLookup(Base):
    """
    Provenance + cache + review-queue record for one ingredient's
    English Wikipedia fallback lookup (see
    `app.services.ingredient_wikipedia_enrichment`).

    One row per ingredient (unique `ingredient_id`), refreshed in place
    on a later retry -- same dedup convention as `ProductSource`
    (one row per (barcode, provider)), just with a single implicit
    provider here. This row is deliberately NOT the same thing as
    `Ingredient.field_provenance_json`: that records which SOURCE
    currently backs each mergeable field (read by
    `ingredient_catalog.resolve_field_source`); this table records what
    the LOOKUP ATTEMPT ITSELF found (page, URL, extract, match status),
    which is also what lets a repeat scan skip a redundant network call
    (`attempted_at`/`retrieved_at` below) and what lets an uncertain or
    unclear case be queued for manual review (`match_status`/
    `review_reason`) without inventing a second ingredient-review
    subsystem.

    Never overwrites curated/regulatory values -- the enrichment path
    may fill a blank description, but records Wikipedia as that field's
    provenance and preserves any higher-ranked row source and every
    nonblank field.
    """

    __tablename__ = "ingredient_wikipedia_lookups"
    __table_args__ = (
        UniqueConstraint("ingredient_id", name="uq_ingredient_wikipedia_lookups_ingredient_id"),
        CheckConstraint(
            "match_status IN ('MATCHED', 'NOT_FOUND', 'AMBIGUOUS', 'SKIPPED_UNCLEAR_NAME', 'ERROR')",
            name="ck_ingredient_wikipedia_lookups_match_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ingredient_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ingredients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The name actually searched -- `Ingredient.common_name` at attempt
    # time, not re-derived later, so the audit trail stays honest even
    # if the ingredient's display name later changes.
    query_text: Mapped[str] = mapped_column(String(255), nullable=False)

    match_status: Mapped[str] = mapped_column(String(32), nullable=False)
    # Closed-vocabulary reason, set only when match_status requires a
    # human look (never set for MATCHED/NOT_FOUND).
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    matched_page_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Short extract actually retrieved (bounded at write time -- see
    # app.integrations.wikipedia_api), never the full page body.
    extracted_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON array of the `Ingredient` field names this lookup actually
    # populated (a subset of the allow-list) -- empty/absent when
    # nothing was written (e.g. NOT_FOUND, ERROR, or every allow-listed
    # field was already non-blank).
    fields_populated_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False, default=0)

    # Set only on a real, successful HTTP fetch that produced a MATCHED
    # result -- drives the positive-cache TTL.
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Updated on every attempt, successful or not -- drives the
    # negative-cache TTL so a NOT_FOUND/ERROR/AMBIGUOUS/
    # SKIPPED_UNCLEAR_NAME result isn't re-attempted on every scan.
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # Short, safe failure description (status code / exception class
    # name) -- never a stack trace, response body, or header.
    error_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
