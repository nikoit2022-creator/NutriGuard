"""Assemble the "required operator review bundle per identity" (see
docs/OPENFOODTOX_PROFILE_PRESENTATION.md) for one catalogue identity's
``exact_match`` result from ``matcher.py``.

This re-parses only the small number of *matched* archives directly
(via ``dossier.build_dossier_record``) rather than requiring a full
catalogue.jsonl rebuild, so a pilot run over a handful of substances
stays fast while still getting the complete per-document fields
(``manifest_links`` etc.) that subject-link resolution needs.

Never computes or infers a numeric consumer-facing "safe to eat"
guidance figure, never promotes a status, and never writes
REVIEWED/VERIFIED anywhere -- every status defaults to an explicit,
honest "not yet done" value.

The draft EN/BG text is a *readable paraphrase*, built entirely from
already-extracted **structured** fields (value type, magnitude, unit,
population, chemical basis, dates, feed-context flag) -- never by
algorithmically rewriting the source's own free-text justification,
which would risk subtly changing a scientific/legal source's meaning.
The source's own justification text is quoted verbatim separately, in
an "internal evidence" section, for a human reviewer to check the
paraphrase against -- the two are never conflated, and an English quote
is never presented as if it were a completed Bulgarian description.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .catalogue_snapshot import CatalogueIdentity
from .codebook import Codebook
from .dossier import build_dossier_record
from .editorial_content import EDITORIAL_CONTENT_VERSION, EditorialEntry, get_editorial_entry
from .matcher import DossierHit, MatchResult, resolve_subject_links

# Exact-equivalent-only BG translations for the specific controlled-vocabulary
# terms this pilot actually encounters, used only in the consumer-facing
# draft text (the underlying extracted record keeps its original,
# machine-readable English value untouched -- see `rv["population_label"]`
# etc. in deduplicated_reference_values). Deliberately a small, curated
# allowlist, not a generic translator: anything not listed here is left in
# its original form rather than guessed at.
_POPULATION_LABEL_BG = {
    "consumers": "общото население (потребители)",
    "general population": "общото население",
}
_UNIT_LABEL_BG = {
    "mg/kg bw/day": "мг/кг телесно тегло дневно",
    "mg/kg bw": "мг/кг телесно тегло",
    "mg/kg": "мг/кг",
    "µg/kg bw/day": "мкг/кг телесно тегло дневно",
}
_BASIS_LABEL_BG = {
    "sodium nitrite": "натриев нитрит",
    "aspartame": "аспартам",
    "citric acid": "лимонена киселина",
}


def _bg_population(label: str | None) -> str:
    if not label:
        return "неустановена популация"
    return _POPULATION_LABEL_BG.get(label.strip().lower(), label)


def _bg_unit(label: str | None) -> str:
    if not label:
        return "неустановена единица"
    return _UNIT_LABEL_BG.get(label.strip(), label)


def _bg_basis(basis: str | None) -> str | None:
    if not basis:
        return None
    return _BASIS_LABEL_BG.get(basis.strip().lower(), basis)


# Explicit, non-technical EN/BG display names for the pilot identities,
# used only for the draft's own H1 heading. Deliberately separate from
# `CatalogueIdentity.common_name`, which for an ad hoc dataset query (e.g.
# E150d, which has no tracked NutriGuard catalogue row) reads literally as
# "(ad hoc query, not a provisioned NutriGuard ingredient: E150d)" --
# correct and important as *operator* metadata (still present verbatim in
# the profile's own `catalogue_identity` block), but never appropriate as
# the consumer-facing ingredient heading. Falls back to `common_name` for
# any identity not in this small, curated map -- never invents a display
# name for a substance this map doesn't cover.
_DISPLAY_NAME_BY_E_NUMBER = {
    "E250": ("Sodium nitrite", "Натриев нитрит"),
    "E150d": ("Sulphite ammonia caramel", "Сулфитно-амонячен карамел"),
    "E330": ("Citric acid", "Лимонена киселина"),
    "E951": ("Aspartame", "Аспартам"),
}


def _display_name(catalogue_identity: CatalogueIdentity) -> tuple[str, str]:
    mapped = _DISPLAY_NAME_BY_E_NUMBER.get(catalogue_identity.e_number_normalized or "")
    if mapped:
        return mapped
    return catalogue_identity.common_name, catalogue_identity.common_name


# Reference-value types that represent a human dietary intake limit at
# all. AOEL/AAOEL are *operator* (occupational: dermal/inhalation/mixed
# handling exposure) levels, never a consumer daily/acute intake limit,
# regardless of how complete or well-sourced the record is -- excluded
# unconditionally, not just when a feed/operator population happens to
# be recorded. ADI = chronic daily intake; ARfD = acute (single-dose)
# intake -- both human-dietary and both retained as distinct, since the
# task requires distinguishing daily/chronic from single-dose
# endpoints; this distinction is carried by value_type itself (no
# separate "period" field exists in the extracted data). "OTHER" covers
# everything else the dataset's own schema does not give a defined
# consumer meaning to (e.g. "margin of safety", species-specific feed
# levels) and is never treated as a consumer limit either.
_CONSUMER_DIETARY_VALUE_TYPES = {"ADI", "ARfD"}

# Fail-closed allowlist: population must be explicitly a general human
# population to be eligible for consumer guidance. Anything else
# (workers, operators, named animal species, or simply absent) is
# excluded -- a blocklist of "bad" populations was deliberately not used,
# since an unrecognized future population label must default to
# ineligible, not eligible.
_ALLOWED_CONSUMER_POPULATION_LABELS = {"consumers", "general population"}

# A dossier whose expert group/regulation marks it as an animal-feed
# (FEEDAP / Regulation (EC) No 1831/2003) assessment rather than a human
# food additive one. Reference values from such a dossier must never be
# presented as human dietary guidance (task: "preserve ... feed-only
# qualifiers").
_FEED_MARKERS = ("FEEDAP", "1831/2003")


def _is_feed_context(dossier_summary: dict) -> bool:
    expert_group = dossier_summary.get("expert_group_label") or ""
    regulation = dossier_summary.get("regulation_label") or ""
    haystack = f"{expert_group} {regulation}"
    return any(marker in haystack for marker in _FEED_MARKERS)


@dataclass
class DossierBundle:
    dossier_file: str
    status: str
    title: str | None = None
    persistent_identifier: str | None = None
    date_of_evaluation: str | None = None
    food_domain_label: str | None = None
    regulation_label: str | None = None
    expert_group_label: str | None = None
    feed_or_livestock_context: bool = False
    reference_substance: dict | None = None
    reference_values: list[dict] | None = None
    human_health_endpoints: list[dict] | None = None
    other_domain_endpoint_counts: dict[str, int] | None = None
    unresolved_items_in_dossier: dict[str, int] | None = None
    evidence_complete: bool = True
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def build_dossier_bundles(
    hits: list[DossierHit],
    dossiers_dir: str,
    codebook: Codebook | None,
    editorial_basis: dict | None = None,
) -> list[DossierBundle]:
    bundles: list[DossierBundle] = []
    for hit in hits:
        archive_path = os.path.join(dossiers_dir, hit.dossier_file)
        record = build_dossier_record(archive_path, hit.dossier_file, codebook)
        if record["status"] != "ok":
            bundles.append(
                DossierBundle(dossier_file=hit.dossier_file, status="extraction_error", errors=record.get("errors", []))
            )
            continue

        linkage = resolve_subject_links(record)
        bucket = linkage["by_identity"].get(hit.rs_document_key)
        if bucket is None:
            bundles.append(
                DossierBundle(
                    dossier_file=hit.dossier_file,
                    status="subject_linkage_lost_on_reparse",
                    errors=[f"reference_substance document_key {hit.rs_document_key!r} not found on re-parse"],
                )
            )
            continue

        rs = next(
            (r for r in record["identity"]["reference_substances"] if r.get("document_key") == hit.rs_document_key),
            None,
        )
        dossier_summary = record.get("dossier_summary") or {}
        feed_context = _is_feed_context(dossier_summary)
        identity_evidence_complete = bool(rs.get("evidence_complete", True)) if rs else False
        reference_values = [
            {
                **rv,
                "feed_or_livestock_context": feed_context,
                "review_eligibility": _assess_review_eligibility(
                    rv,
                    feed_context=feed_context,
                    identity_evidence_complete=identity_evidence_complete,
                    editorial_basis=editorial_basis,
                ).to_dict(),
            }
            for rv in bucket["reference_values"]
        ]
        human_health_endpoints = bucket["endpoints"].get("human_health", [])
        other_domain_counts = {k: len(v) for k, v in bucket["endpoints"].items() if k != "human_health"}
        unresolved_here = {
            "reference_values": sum(
                1
                for rv in linkage["unresolved"]["reference_values"]
                if rv.get("source", {}).get("archive") == hit.dossier_file
            ),
            "endpoints": sum(
                1 for ep in linkage["unresolved"]["endpoints"] if ep.get("source", {}).get("archive") == hit.dossier_file
            ),
        }
        bundles.append(
            DossierBundle(
                dossier_file=hit.dossier_file,
                status="ok",
                title=record.get("title"),
                persistent_identifier=dossier_summary.get("persistent_identifier"),
                date_of_evaluation=dossier_summary.get("date_of_evaluation"),
                food_domain_label=dossier_summary.get("food_domain_label"),
                regulation_label=dossier_summary.get("regulation_label"),
                expert_group_label=dossier_summary.get("expert_group_label"),
                feed_or_livestock_context=feed_context,
                reference_substance=rs,
                reference_values=reference_values,
                human_health_endpoints=human_health_endpoints,
                other_domain_endpoint_counts=other_domain_counts,
                unresolved_items_in_dossier=unresolved_here,
                evidence_complete=bool(rs.get("evidence_complete", True)) and not record["counts"].get(
                    "documents_with_incomplete_evidence"
                ),
            )
        )
    return bundles


@dataclass
class ReviewEligibility:
    # Operators must always be able to inspect extracted evidence --
    # including incomplete or inapplicable records, so they can make
    # their own judgement call -- so this is never set False here; a
    # record is only ever absent from a bundle entirely when subject-link
    # resolution itself failed (see resolve_subject_links' "unresolved"
    # bucket, never surfaced in a profile at all).
    operator_inspectable: bool
    # Strict: only true when the record could, in principle, support a
    # numeric human dietary intake claim once scientifically reviewed --
    # never true while scientific_review stays "not_reviewed" at the
    # profile level, and never a promise that the number itself is sound,
    # only that the record's *shape* (type/population/basis/unit/
    # completeness) is the applicable shape for one.
    consumer_guidance_eligible: bool
    reasons: list[str] = field(default_factory=list)
    # "automated" (chemical_basis.py resolved it from the record's own
    # text), "editorial_override" (the automated check did not resolve
    # it, but this session directly confirmed the basis by reading
    # primary source text -- see editorial_content.py -- and recorded an
    # explicit citation for it), or "unresolved" (neither).
    basis_source: str = "unresolved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_inspectable": self.operator_inspectable,
            "consumer_guidance_eligible": self.consumer_guidance_eligible,
            "reasons": self.reasons,
            "basis_source": self.basis_source,
        }


def _assess_review_eligibility(
    rv: dict, *, feed_context: bool, identity_evidence_complete: bool, editorial_basis: dict | None = None
) -> ReviewEligibility:
    """Distinguish "safe for an operator to inspect" (always true -- a
    record is never hidden from review for being incomplete or
    inapplicable) from "eligible for numeric human intake guidance"
    (strict and fail-closed: every one of completeness, resolved subject
    linkage, a known unit, an applicable reference-value type, and a
    general-consumer population must hold; chemical basis must be either
    automated-resolved or editorially confirmed -- never guessed).
    Every reason a record fails the strict check is recorded, never
    silently dropped, so an operator sees exactly why."""
    reasons: list[str] = []

    rv_complete = rv.get("evidence_complete")
    if rv_complete is not True:
        # Missing/None is treated the same as an explicit False --
        # fail-closed on an absent flag, never assumed complete.
        reasons.append("reference-value extraction itself is flagged incomplete (or completeness was not recorded)")
    if not identity_evidence_complete:
        reasons.append("the matched identity's own (REFERENCE_SUBSTANCE) extraction was incomplete")

    subject_basis = rv.get("subject_linkage_basis")
    if subject_basis not in ("single_identity_dossier", "manifest_child_link"):
        reasons.append(f"subject linkage was not cleanly resolved (basis={subject_basis!r})")

    basis_status = (rv.get("chemical_basis") or {}).get("status")
    if basis_status == "resolved":
        basis_source = "automated"
    elif editorial_basis is not None:
        # The automated text-pattern match did not resolve it, but this
        # session has directly read the primary source and recorded an
        # explicit, citable confirmation -- never a silent guess. See
        # editorial_content.py's module docstring for when this is and
        # is not used.
        basis_source = "editorial_override"
    else:
        reasons.append(f"chemical basis is not resolved (status={basis_status!r})")
        basis_source = "unresolved"
    if not rv.get("unit_label"):
        reasons.append("unit was not resolved")

    value_type = rv.get("value_type")
    if value_type not in _CONSUMER_DIETARY_VALUE_TYPES:
        reasons.append(
            f"{value_type!r} is not a consumer dietary reference-value type -- AOEL/AAOEL are operator "
            "(occupational) exposure levels, never a consumer intake limit, and OTHER has no defined "
            "consumer daily/acute meaning in this dataset's own schema"
        )

    population_label = (rv.get("population_label") or "").strip().lower()
    if population_label not in _ALLOWED_CONSUMER_POPULATION_LABELS:
        reasons.append(f"population {rv.get('population_label')!r} is not a recognized general-consumer population")

    if feed_context:
        reasons.append("sourced from an animal-feed/non-food (FEEDAP) assessment, not a human food evaluation")

    return ReviewEligibility(
        operator_inspectable=True, consumer_guidance_eligible=not reasons, reasons=reasons, basis_source=basis_source
    )


def _dedupe_reference_values(bundles: list[DossierBundle]) -> list[dict]:
    """Group reference values that restate the *same* claim across
    multiple assessments of one substance (task: "Multiple assessments
    of one substance are not duplicate substances" -- but repeating the
    identical justification text verbatim once per assessment in a
    consumer-facing draft would violate the presentation spec's "avoid
    repetitive sections"). Grouping key is the claim's own content, not
    just the dossier -- a genuinely revised figure in a later assessment
    is kept as its own entry, never silently merged into an older one.
    """
    groups: dict[tuple, dict] = {}
    for b in bundles:
        if b.status != "ok":
            continue
        for rv in b.reference_values or []:
            key = (
                rv.get("value_type"),
                rv.get("value"),
                rv.get("lower_value"),
                rv.get("upper_value"),
                rv.get("unit_label"),
                rv.get("population_label"),
                rv.get("justification_and_comments"),
                rv.get("feed_or_livestock_context"),
            )
            if key not in groups:
                groups[key] = {**rv, "asserted_in": []}
            groups[key]["asserted_in"].append({"dossier_file": b.dossier_file, "date_of_evaluation": b.date_of_evaluation, "title": b.title})
    return list(groups.values())


def _group_for_consumer_display(values: list[dict]) -> list[dict]:
    """Coarser grouping than `_dedupe_reference_values`, used only for the
    consumer-facing draft text: several assessments that state the exact
    same type/magnitude/unit/population (varying only in incidental
    justification wording, e.g. E951's five near-identical ADI
    restatements) are rendered as ONE statement citing every contributing
    date, instead of repeating near-identical paragraphs once per
    assessment (presentation spec: "avoid repetitive sections"). Nothing
    is discarded -- the finer-grained per-assessment detail (each exact
    quote) remains available in `deduplicated_reference_values`/
    `internal_evidence_en`, which iterate the un-merged list. Eligibility
    for a merged group is the AND of every member's own eligibility
    (fail-closed: one incomplete/ineligible assessment of an otherwise
    identical claim is enough to withhold the whole group's number)."""
    groups: dict[tuple, dict] = {}
    member_eligibilities: dict[tuple, list[dict]] = {}
    for rv in values:
        key = (
            rv.get("value_type"),
            rv.get("value"),
            rv.get("lower_value"),
            rv.get("upper_value"),
            rv.get("unit_label"),
            rv.get("population_label"),
        )
        if key not in groups:
            groups[key] = {**rv, "asserted_in": []}
            member_eligibilities[key] = []
        groups[key]["asserted_in"].extend(rv["asserted_in"])
        member_eligibilities[key].append(rv["review_eligibility"])

    merged: list[dict] = []
    for key, g in groups.items():
        elig_list = member_eligibilities[key]
        all_eligible = all(e["consumer_guidance_eligible"] for e in elig_list)
        reasons = sorted({r for e in elig_list for r in e["reasons"]})
        basis_sources = {e["basis_source"] for e in elig_list}
        basis_source = (
            "automated" if "automated" in basis_sources else "editorial_override" if "editorial_override" in basis_sources else "unresolved"
        )
        g["review_eligibility"] = {
            "operator_inspectable": True,
            "consumer_guidance_eligible": all_eligible,
            "reasons": reasons,
            "basis_source": basis_source,
        }
        g["asserted_in"] = sorted(g["asserted_in"], key=lambda a: a.get("date_of_evaluation") or "")
        merged.append(g)
    return merged


_VALUE_TYPE_LABEL_EN = {
    "ADI": "Acceptable Daily Intake (ADI)",
    "ARfD": "Acute Reference Dose (ARfD)",
    "AOEL": "Acceptable Operator Exposure Level (AOEL -- an occupational, not a consumer, limit)",
    "AAOEL": "Acute Acceptable Operator Exposure Level (AAOEL -- an occupational, not a consumer, limit)",
    "OTHER": "a non-standard reference value",
}
_VALUE_TYPE_LABEL_BG = {
    "ADI": "допустима дневна доза (ADI)",
    "ARfD": "остра референтна доза (ARfD)",
    "AOEL": "допустимо ниво на експозиция на оператора (AOEL -- показател за условия на труд, не за потребители)",
    "AAOEL": "остро допустимо ниво на експозиция на оператора (AAOEL -- показател за условия на труд, не за потребители)",
    "OTHER": "нестандартна референтна стойност",
}


def _claim_dates(rv: dict) -> list[str]:
    return sorted({a["date_of_evaluation"] for a in rv["asserted_in"] if a.get("date_of_evaluation")})


def _claim_citation(rv: dict) -> str:
    titles = sorted({a["title"] for a in rv["asserted_in"] if a.get("title")})
    dates = _claim_dates(rv)
    parts = []
    if titles:
        parts.append("; ".join(titles))
    if dates:
        parts.append(f"({', '.join(dates)})")
    return " ".join(parts) if parts else "(source dossier not identified)"


def _short_citation(rv: dict) -> str:
    """A concise inline citation (dates only) for a paragraph -- the full
    title/DOI already lives once in the Sources section, so paragraphs
    are not overwhelmed by repeating it (task: "should not overwhelm each
    paragraph")."""
    dates = _claim_dates(rv)
    return f"({', '.join(dates)})" if dates else "(date not extracted)"


def _resolved_basis_text(rv: dict, editorial: EditorialEntry | None) -> tuple[str | None, str | None]:
    """Returns (basis_en, basis_bg) -- from the automated field when
    resolved, from the editorial override when that's how this record
    became eligible, else (None, None). Never guesses."""
    basis_source = (rv.get("review_eligibility") or {}).get("basis_source")
    if basis_source == "automated":
        basis = (rv.get("chemical_basis") or {}).get("basis")
        return basis, _bg_basis(basis)
    if basis_source == "editorial_override" and editorial and editorial.editorial_chemical_basis:
        eb = editorial.editorial_chemical_basis
        return eb["basis_en"], eb["basis_bg"]
    return None, None


def _finding_sentence_en(rv: dict, editorial: EditorialEntry | None) -> str:
    """Describes one finding for "Effects and conditions". Shows the
    actual magnitude only when the record is consumer_guidance_eligible
    -- the exact same test "Intake guidance" uses (task: "moving a number
    between headings must not bypass the same ... eligibility rules")."""
    eligible = rv["review_eligibility"]["consumer_guidance_eligible"]
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_EN.get(value_type, value_type or "a reference value")
    n = len(rv["asserted_in"])
    cite = _short_citation(rv)
    reaffirmed = f" Reaffirmed across {n} separate assessments." if n > 1 else ""
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")

    if eligible and magnitude and unit:
        basis_en, _ = _resolved_basis_text(rv, editorial)
        basis_phrase = f", chemical basis: {basis_en}" if basis_en else ""
        return f"EFSA set {type_label} of {magnitude} {unit} for the general consumer population{basis_phrase}. {cite}{reaffirmed}"
    if not magnitude:
        return f"EFSA's opinion on {type_label} did not state a specific numeric limit for this identity in the extracted text. {cite} See internal evidence for the source's own wording."
    return (
        f"EFSA's opinion references {type_label} for this identity; the specific figure is not included "
        f"in this preview yet. {cite} See internal evidence and Sources for detail."
    )


def _finding_sentence_bg(rv: dict, editorial: EditorialEntry | None) -> str:
    """BG counterpart of `_finding_sentence_en` -- an independent
    rendering of the same structured facts and the same eligibility
    gate, not a translation of the EN string."""
    eligible = rv["review_eligibility"]["consumer_guidance_eligible"]
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_BG.get(value_type, value_type or "референтна стойност")
    n = len(rv["asserted_in"])
    cite = _short_citation(rv)
    reaffirmed = f" Потвърдено в {n} отделни оценки." if n > 1 else ""
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")

    if eligible and magnitude and unit:
        _, basis_bg = _resolved_basis_text(rv, editorial)
        basis_phrase = f", химична основа: {basis_bg}" if basis_bg else ""
        return f"ЕФСА определя {type_label} от {magnitude} {_bg_unit(unit)} за общото население (потребители){basis_phrase}. {cite}{reaffirmed}"
    if not magnitude:
        return f"Становището на ЕФСА относно {type_label} не посочва конкретна числена граница за тази идентичност в извлечения текст. {cite} Вижте вътрешния цитат за точната формулировка на източника."
    return (
        f"Становището на ЕФСА споменава {type_label} за тази идентичност; конкретната стойност все още не "
        f"е включена в този преглед. {cite} Вижте вътрешния цитат и източниците за подробности."
    )


_PERIOD_EN = {"ADI": "a chronic, daily intake limit", "ARfD": "an acute, single-dose intake limit"}
_PERIOD_BG = {"ADI": "хроничен, дневен лимит на прием", "ARfD": "остър лимит на прием при еднократна доза"}


def _intake_sentence_en(rv: dict, editorial: EditorialEntry | None) -> str:
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_EN.get(value_type, value_type)
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")
    basis_en, _ = _resolved_basis_text(rv, editorial)
    period = _PERIOD_EN.get(value_type, "an intake limit")
    cite = _short_citation(rv)
    n = len(rv["asserted_in"])
    reaffirmed = f" Established/reaffirmed across {n} separate EFSA assessments." if n > 1 else ""
    text = f"{type_label}: {magnitude} {unit}, for the general consumer population, expressed as {period}"
    if basis_en:
        text += f" (chemical basis: {basis_en})"
    text += f". {cite}{reaffirmed}"
    text += (
        " This is a regulatory reference threshold, not a recommended target intake and not a product "
        "portion size -- it does not by itself indicate how much of any specific product can be safely "
        "consumed, which depends on that product's actual ingredient concentration (not calculated here)."
    )
    return text


def _intake_sentence_bg(rv: dict, editorial: EditorialEntry | None) -> str:
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_BG.get(value_type, value_type)
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")
    _, basis_bg = _resolved_basis_text(rv, editorial)
    period = _PERIOD_BG.get(value_type, "лимит на прием")
    cite = _short_citation(rv)
    n = len(rv["asserted_in"])
    reaffirmed = f" Установено/потвърдено в {n} отделни оценки на ЕФСА." if n > 1 else ""
    text = f"{type_label}: {magnitude} {_bg_unit(unit)}, за общото население (потребители), изразено като {period}"
    if basis_bg:
        text += f" (химична основа: {basis_bg})"
    text += f". {cite}{reaffirmed}"
    text += (
        " Това е регулаторен референтен праг, а не препоръчителна целева доза и не е размер на порция "
        "продукт -- само по себе си не показва какво точно количество от конкретен продукт може да се "
        "консумира безопасно, което зависи от действителната концентрация на съставката в продукта (не е "
        "изчислено тук)."
    )
    return text


def _internal_evidence_entry(rv: dict) -> str:
    """The verbatim source quote this claim's paraphrase is built from,
    kept in its own section and never presented as a BG description nor
    merged into the paraphrase itself."""
    quote = rv.get("justification_and_comments") or "(no justification text extracted)"
    citation = _claim_citation(rv)
    return f"[{rv.get('value_type')}, {citation}] “{quote}”"


_EVIDENCE_TYPE_TAG_EN = {
    "human": "(human evidence)",
    "animal_in_vitro": "(animal/in-vitro evidence)",
    "assessment_conclusion": "(assessment conclusion)",
}
_EVIDENCE_TYPE_TAG_BG = {
    "human": "(данни при хора)",
    "animal_in_vitro": "(данни от животни/ин витро)",
    "assessment_conclusion": "(заключение на оценката)",
}


def build_profile(
    catalogue_identity: CatalogueIdentity,
    match_result: MatchResult,
    dossiers_dir: str,
    codebook: Codebook | None,
) -> dict[str, Any]:
    """Build the full per-identity operator review bundle. Never invoked
    for anything but an ``exact_match`` -- callers must report
    no_match/ambiguous/conflicting results as-is from ``matcher.py``
    without a draft (there is nothing safe to draft text about)."""
    if match_result.overall_status != "exact_match":
        raise ValueError("build_profile only applies to an exact_match MatchResult")

    editorial = get_editorial_entry(catalogue_identity.e_number_normalized)
    editorial_basis = editorial.editorial_chemical_basis if editorial else None

    bundles = build_dossier_bundles(match_result.exact_dossiers, dossiers_dir, codebook, editorial_basis=editorial_basis)
    ok_bundles = [b for b in bundles if b.status == "ok"]
    extraction_errors = [b for b in bundles if b.status != "ok"]

    deduped_values = _dedupe_reference_values(ok_bundles)
    # Feed/worker (FEEDAP, occupational) findings are kept in full in
    # deduped_values/dossier_bundles for operator inspection, but never
    # appear anywhere in the consumer-facing draft at all (task: "Keep
    # feed/worker guidance out of the consumer preview, retaining it only
    # in operator evidence") -- not merely caveated inline as before.
    consumer_values = [v for v in deduped_values if not v.get("feed_or_livestock_context")]
    # Coarser grouping for the draft text only -- see _group_for_consumer_display.
    display_values = _group_for_consumer_display(consumer_values)
    human_health_total = sum(len(b.human_health_endpoints or []) for b in ok_bundles)
    all_evidence_complete = all(b.evidence_complete for b in ok_bundles) if ok_bundles else False

    statuses = {
        "identity_match": "exact" if ok_bundles else "unresolved_on_reparse",
        "extraction_completeness": "complete" if all_evidence_complete else "incomplete_some_truncated",
        "scientific_review": "not_reviewed",
        "translation_review": "not_reviewed",
        "reuse_clearance": "pending_external_check",
        "source_freshness": "checked_limited_scope",
    }

    if not ok_bundles:
        outcome = "blocked"
    elif not deduped_values and human_health_total == 0:
        outcome = "missing_evidence"
    else:
        outcome = "ready_for_human_review"

    claim_matrix: list[dict[str, str]] = []

    def _claim(section: str, claim_en: str, source_kind: str, source: str) -> None:
        claim_matrix.append({"section": section, "claim_en": claim_en, "source_kind": source_kind, "source": source})

    omitted_sections: list[dict[str, str]] = []
    en_sections: list[str] = []
    bg_sections: list[str] = []

    # --- What it is / Purpose in food -----------------------------------
    if editorial and editorial.identity:
        en_sections.append(f"## What it is\n\n{editorial.identity.en}")
        bg_sections.append(f"## Какво представлява\n\n{editorial.identity.bg}")
        _claim("what_it_is", editorial.identity.en, editorial.identity.source_kind, editorial.identity.source)
    else:
        omitted_sections.append({"section": "What it is", "reason": "no sourced identity/origin content available for this identity"})

    if editorial and editorial.purpose:
        en_sections.append(f"## Purpose in food\n\n{editorial.purpose.en}")
        bg_sections.append(f"## Роля в храната\n\n{editorial.purpose.bg}")
        _claim("purpose_in_food", editorial.purpose.en, editorial.purpose.source_kind, editorial.purpose.source)
    else:
        omitted_sections.append({"section": "Purpose in food", "reason": "no sourced technological-function content available for this identity"})

    # --- Effects and conditions ------------------------------------------
    effects_en: list[str] = []
    effects_bg: list[str] = []
    has_assessment_conclusion = False
    if editorial:
        for note in editorial.effects:
            effects_en.append(f"{note.text.en} {_EVIDENCE_TYPE_TAG_EN[note.evidence_type]}")
            effects_bg.append(f"{note.text.bg} {_EVIDENCE_TYPE_TAG_BG[note.evidence_type]}")
            _claim("effects", note.text.en, note.text.source_kind, note.text.source)
            if note.evidence_type == "assessment_conclusion":
                has_assessment_conclusion = True

    # Population exceptions (e.g. PKU) must sit adjacent to a shown ADI or
    # general-population safety conclusion, not be buried under Sources --
    # placed once here (right after any assessment-conclusion narrative)
    # and again in Intake guidance below if that section exists, rather
    # than after every single sentence (which would itself violate "avoid
    # repetitive sections").
    if editorial and editorial.population_exceptions and has_assessment_conclusion:
        for exc in editorial.population_exceptions:
            effects_en.append(exc.en)
            effects_bg.append(exc.bg)
            _claim("effects_population_exception", exc.en, exc.source_kind, exc.source)

    for rv in display_values:
        finding_en = _finding_sentence_en(rv, editorial)
        effects_en.append(finding_en)
        effects_bg.append(_finding_sentence_bg(rv, editorial))
        _claim("effects_reference_value", finding_en, "openfoodtox_dossier", _claim_citation(rv))
        if rv["review_eligibility"]["consumer_guidance_eligible"] and editorial and editorial.population_exceptions and not has_assessment_conclusion:
            # Only reached if no editorial assessment-conclusion note already carried the exception above.
            for exc in editorial.population_exceptions:
                effects_en.append(exc.en)
                effects_bg.append(exc.bg)
                _claim("effects_population_exception", exc.en, exc.source_kind, exc.source)

    if editorial and editorial.group_scope_note:
        effects_en.append(editorial.group_scope_note.en)
        effects_bg.append(editorial.group_scope_note.bg)
        _claim("effects_group_scope", editorial.group_scope_note.en, editorial.group_scope_note.source_kind, editorial.group_scope_note.source)

    if editorial and editorial.operator_only_notes:
        for note in editorial.operator_only_notes:
            _claim("operator_only", note.en, note.source_kind, note.source)

    if effects_en:
        en_sections.append("## Effects and conditions\n\n" + "\n\n".join(effects_en))
        bg_sections.append("## Ефекти и условия\n\n" + "\n\n".join(effects_bg))
    else:
        en_sections.append("## Effects and conditions\n\n(No substantive effects evidence was extracted for this identity in the matched dossiers; hidden rather than shown empty.)")
        bg_sections.append("## Ефекти и условия\n\n(За тази идентичност не са извлечени съществени данни за ефекти от съпоставените досиета.)")

    internal_evidence = [_internal_evidence_entry(v) for v in deduped_values]

    # --- Intake guidance ---------------------------------------------------
    eligible_values = [v for v in display_values if v["review_eligibility"]["consumer_guidance_eligible"]]
    if eligible_values:
        intake_en: list[str] = []
        intake_bg: list[str] = []
        for rv in eligible_values:
            intake_sentence_en = _intake_sentence_en(rv, editorial)
            intake_en.append(intake_sentence_en)
            intake_bg.append(_intake_sentence_bg(rv, editorial))
            _claim("intake_guidance", intake_sentence_en, "openfoodtox_dossier", _claim_citation(rv))
            if editorial and editorial.population_exceptions:
                for exc in editorial.population_exceptions:
                    intake_en.append(exc.en)
                    intake_bg.append(exc.bg)
                    _claim("intake_guidance_population_exception", exc.en, exc.source_kind, exc.source)
        en_sections.append("## Intake guidance\n\n" + "\n\n".join(intake_en))
        bg_sections.append("## Насоки за прием\n\n" + "\n\n".join(intake_bg))
    else:
        omitted_sections.append(
            {
                "section": "Intake guidance",
                "reason": "no reference value for this identity is consumer_guidance_eligible (see each value's review_eligibility.reasons)",
            }
        )

    # --- Sources -------------------------------------------------------------
    sources_en = ["## Sources"]
    sources_bg = ["## Източници"]
    for b in ok_bundles:
        cite = f"- {b.title} ({b.date_of_evaluation or 'date not extracted'})"
        if b.persistent_identifier:
            cite += f" -- {b.persistent_identifier}"
        sources_en.append(cite)
        sources_bg.append(cite)  # citations are not translated (titles/DOIs stay as published)
    if editorial and editorial.external_sources:
        sources_en.append("\n**External sources** (editorial content for this review task, distinct from OpenFoodTox-extracted evidence above):")
        sources_bg.append("\n**Външни източници** (редакционно съдържание за тази задача за преглед, отделно от извлечените по-горе доказателства от OpenFoodTox):")
        for s in editorial.external_sources:
            line = f"- {s['title']} -- {s['url']} (accessed {s['access_date']})"
            if s.get("note"):
                line += f" {s['note']}"
            sources_en.append(line)
            sources_bg.append(line)  # titles/URLs intentionally kept in their original language
    en_sections.append("\n".join(sources_en))
    bg_sections.append("\n".join(sources_bg))

    display_name_en, display_name_bg = _display_name(catalogue_identity)
    header_en = f"# {display_name_en} ({catalogue_identity.e_number_raw})\n\nStatus: DRAFT -- not scientifically reviewed, not translation-reviewed, not publication-ready."
    header_bg = f"# {display_name_bg} ({catalogue_identity.e_number_raw})\n\nСтатус: ЧЕРНОВА -- не е научно прегледано, не е прегледан преводът, не е готово за публикуване."

    remaining_uncertainties = [o["reason"] for o in omitted_sections]
    for rv in display_values:
        if not rv["review_eligibility"]["consumer_guidance_eligible"]:
            remaining_uncertainties.append(
                f"{rv.get('value_type')} ({_claim_citation(rv)}): " + "; ".join(rv["review_eligibility"]["reasons"])
            )
    if editorial_basis:
        remaining_uncertainties.append(
            f"Chemical basis for the eligible reference value above (shown in the draft as "
            f"{editorial_basis['basis_en']!r}) relies on this session's direct reading of the primary "
            f"source text (editorial_override), not on the automated chemical_basis.py match: "
            f"{editorial_basis.get('explanation_en', '')} Source: {editorial_basis.get('source', '')}"
        )

    return {
        "catalogue_identity": catalogue_identity.to_dict(),
        "match_summary": {
            "overall_status": match_result.overall_status,
            "explanation": match_result.explanation,
            "exact_dossier_count": len(match_result.exact_dossiers),
            "conflicting_dossier_count": len(match_result.conflicting_dossiers),
            "ambiguous_dossier_count": len(match_result.ambiguous_dossiers),
        },
        "dossier_bundles": [b.to_dict() for b in bundles],
        "extraction_errors": [b.to_dict() for b in extraction_errors],
        "deduplicated_reference_values": deduped_values,
        "consumer_draft_excluded_feed_or_worker_value_count": len(deduped_values) - len(consumer_values),
        "human_health_endpoint_count": human_health_total,
        "editorial_content_version": EDITORIAL_CONTENT_VERSION if editorial else None,
        "editorial_chemical_basis": editorial_basis,
        # Operator-only editorial notes (e.g. externally-sourced numeric
        # figures withheld from the consumer preview under this round's
        # single gating policy -- see editorial_content.py) -- never
        # included in draft_en/draft_bg, kept here for operator review.
        "editorial_operator_only_notes": [n.to_dict() for n in editorial.operator_only_notes] if editorial else [],
        "claim_source_matrix": claim_matrix,
        "remaining_uncertainties": remaining_uncertainties,
        "omitted_sections_internal_note": omitted_sections,
        "statuses": statuses,
        "outcome": outcome,
        "draft_en": header_en + "\n\n" + "\n\n".join(en_sections),
        "draft_bg": header_bg + "\n\n" + "\n\n".join(bg_sections),
        "internal_evidence_en": "## Internal evidence (verbatim source quotations -- not for direct publication)\n\n"
        + ("\n\n".join(internal_evidence) if internal_evidence else "(none extracted)"),
    }
