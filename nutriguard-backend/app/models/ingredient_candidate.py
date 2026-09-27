from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base

# Closed vocabulary (also enforced by the CHECK constraint below).
CANDIDATE_STATUSES = ("PENDING", "FLAGGED", "JUNK")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngredientCandidate(Base):
    """
    Issue #23 (stage 2): one bounded OBSERVATION record per distinct
    ingredient token that had no curated identity when it was scanned.

    Three things are kept apart on purpose:

      * OBSERVATION -- this table: how often and when a token was seen,
        plus closed-vocabulary review flags. Nothing here is evidence.
      * CANONICAL IDENTITY -- `ingredients` / `ingredient_aliases`, reused
        exactly as before. `ingredient_id` only POINTS at the identity the
        token resolved to (NULL for junk, which never becomes an identity).
        This is not a second catalog: it holds no scientific field, no
        alias, no verification state.
      * SCIENTIFIC EVIDENCE -- untouched. Seeing a token again raises
        `encounter_count`; it never changes `Ingredient.verification_status`,
        `confidence` or any content field.

    Bounds (all enforced in `app.services.ingredient_candidates`):
    `display_name` and `normalized_key` are capped at 128 characters, no
    image or label text is stored (only the one token), the row count is
    capped by `INGREDIENT_CANDIDATE_MAX_ROWS`, and the manual `prune`
    command removes only stale, rarely seen, unflagged rows -- never an
    `ingredients` row and never a product reference.

    `ingredient_id` is `ON DELETE SET NULL`: removing an identity row later
    keeps the observation.
    """

    __tablename__ = "ingredient_candidates"
    __table_args__ = (
        UniqueConstraint("normalized_key", name="uq_ingredient_candidates_normalized_key"),
        CheckConstraint("status IN ('PENDING', 'FLAGGED', 'JUNK')", name="ck_ingredient_candidates_status"),
        CheckConstraint("encounter_count >= 1", name="ck_ingredient_candidates_encounter_count"),
        Index("ix_ingredient_candidates_last_seen_at", "last_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # `candidate_key(name)`: the normalized name (or prefix + hash when very
    # long). Exact-text dedup only: no stemming, no fuzzy or translated match.
    normalized_key: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # An official identifier actually present in the token text.
    e_number: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    # The identity (catalog row) this token resolved to; NULL for junk.
    ingredient_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ingredients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    # Sorted, comma-joined `CandidateFlag` codes; empty when none.
    flags: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # Number of distinct scan requests that contained the token.
    encounter_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
