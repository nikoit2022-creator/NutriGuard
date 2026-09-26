from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base

SUMMARY_SCOPES = ("E_NUMBER_GENERIC", "INGREDIENT_NAME")
SUMMARY_EVIDENCE_STATES = ("DRAFT", "SOURCE_VERIFIED")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IngredientSummary(Base):
    """
    Issue #23 (stage 3): a stored, source-backed, sectioned summary
    (ORIGIN / FUNCTION / EFFECTS / JURISDICTION) for one subject. Written
    once by the loader, never generated per scan, never a paid lookup.

    It is a SEPARATE layer next to `ingredients`, not a set of columns on
    it, because it is reusable and family-scoped: `subject_key` is either
    an official E-number (`E150D`, scope `E_NUMBER_GENERIC`) or an exact
    normalized ingredient name (`name:sugar`, scope `INGREDIENT_NAME`).
    A generic E-number summary is served BESIDE an ingredient's own
    fields and never merged into or over them, so it cannot overwrite an
    ingredient-specific fact (for example the soy-specific row for E322).

    `evidence_state`: `SOURCE_VERIFIED` means the authoring step checked
    each claim against the cited source text (see `claims_json`, the
    per-claim ledger, which is internal and not served). It is NOT a human
    scientific review; `human_reviewed` says so and stays false until a
    reviewer flips it. Only `SOURCE_VERIFIED` rows are served.

    `content_hash` fingerprints the English sections and citations, so a
    translation of older text is detected and not served.
    """

    __tablename__ = "ingredient_summaries"
    __table_args__ = (
        UniqueConstraint("subject_key", name="uq_ingredient_summaries_subject_key"),
        CheckConstraint("scope IN ('E_NUMBER_GENERIC', 'INGREDIENT_NAME')", name="ck_ingredient_summaries_scope"),
        CheckConstraint("evidence_state IN ('DRAFT', 'SOURCE_VERIFIED')", name="ck_ingredient_summaries_evidence_state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subject_key: Mapped[str] = mapped_column(String(96), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence_state: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    human_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # JSON text: ordered English sections, citations, and the internal
    # per-claim ledger. Text (not JSON type) for SQLite/PostgreSQL parity.
    sections_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    citations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    claims_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # When the sources were last checked (not when the row was written).
    source_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    localizations: Mapped[list["IngredientSummaryLocalization"]] = relationship(
        back_populates="summary", cascade="all, delete-orphan", lazy="selectin"
    )


class IngredientSummaryLocalization(Base):
    """A translation of one summary's sections. Served only when
    `translation_status` is `REVIEWED` AND `source_content_hash` equals the
    summary's current `content_hash`; a draft or stale translation is never
    served and clients fall back to the English sections. A machine
    translation that a person later reviews stays `MACHINE_TRANSLATED`:
    review status and origin are separate, as in `ingredient_localizations`."""

    __tablename__ = "ingredient_summary_localizations"
    __table_args__ = (
        CheckConstraint("translation_status IN ('DRAFT', 'REVIEWED')", name="ck_ingredient_summary_loc_status"),
        CheckConstraint(
            "translation_source IN ('MACHINE_TRANSLATED', 'HUMAN_CURATED')", name="ck_ingredient_summary_loc_source"
        ),
        CheckConstraint("language IN ('bg')", name="ck_ingredient_summary_loc_language"),
    )

    summary_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ingredient_summaries.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(String(2), primary_key=True)
    sections_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    translation_status: Mapped[str] = mapped_column(String(12), nullable=False, default="DRAFT")
    translation_source: Mapped[str] = mapped_column(String(24), nullable=False, default="MACHINE_TRANSLATED")
    source_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free-text reviewer label recorded at approval; never served.
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    summary: Mapped["IngredientSummary"] = relationship(back_populates="localizations")
