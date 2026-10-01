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
from .matcher import DossierHit, MatchResult, resolve_subject_links

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
    hits: list[DossierHit], dossiers_dir: str, codebook: Codebook | None
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
                    rv, feed_context=feed_context, identity_evidence_complete=identity_evidence_complete
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator_inspectable": self.operator_inspectable,
            "consumer_guidance_eligible": self.consumer_guidance_eligible,
            "reasons": self.reasons,
        }


def _assess_review_eligibility(rv: dict, *, feed_context: bool, identity_evidence_complete: bool) -> ReviewEligibility:
    """Distinguish "safe for an operator to inspect" (always true -- a
    record is never hidden from review for being incomplete or
    inapplicable) from "eligible for numeric human intake guidance"
    (strict and fail-closed: every one of completeness, resolved subject
    linkage, resolved chemical basis, a known unit, an applicable
    reference-value type, and a general-consumer population must hold).
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
    if basis_status != "resolved":
        reasons.append(f"chemical basis is not resolved (status={basis_status!r})")
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

    return ReviewEligibility(operator_inspectable=True, consumer_guidance_eligible=not reasons, reasons=reasons)


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


def _paraphrase_reference_value_en(rv: dict) -> str:
    """A readable EN paraphrase built only from structured, already-
    extracted fields (never from reformatting the free-text
    justification -- see module docstring). The verbatim source text
    lives separately in the internal evidence section this claim links
    to via `_claim_citation`."""
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_EN.get(value_type, value_type or "an unspecified reference value")
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")
    population = rv.get("population_label")
    basis = (rv.get("chemical_basis") or {}).get("basis")
    n = len(rv["asserted_in"])
    citation = _claim_citation(rv)

    if rv.get("feed_or_livestock_context"):
        lead = f"An animal-feed (not human food) assessment considered {type_label}"
        if magnitude and unit:
            lead += f" of {magnitude} {unit}"
        if population:
            lead += f" for the population described as {population!r}"
        lead += "."
        tail = " This is a feed/occupational-context finding and is not applicable to human dietary guidance."
    elif magnitude and unit and population:
        basis_phrase = f", with a chemical basis of {basis}" if basis else " (the figure's exact chemical basis was not resolved from the source text)"
        lead = f"EFSA set {type_label} of {magnitude} {unit} for the general {population} population{basis_phrase}."
        tail = f" Reaffirmed in {n} separate EFSA assessments." if n > 1 else ""
    elif not magnitude:
        lead = f"EFSA's opinion on {type_label} did not state a specific numeric limit for this identity in the extracted text."
        tail = " See the internal evidence quote below for the source's own wording."
    else:
        lead = f"EFSA recorded {type_label} with some fields ({'unit' if not unit else ''}{' and ' if not unit and not population else ''}{'population' if not population else ''}) not resolved from the source text."
        tail = ""
    return f"{lead}{tail} (Source: {citation}.)"


def _paraphrase_reference_value_bg(rv: dict) -> str:
    """BG counterpart of `_paraphrase_reference_value_en`, expressing the
    same certainty and conditions -- not a translation of the EN string,
    but an independent rendering of the same structured facts, so a
    reviewer can check either against the same underlying data."""
    value_type = rv.get("value_type")
    type_label = _VALUE_TYPE_LABEL_BG.get(value_type, value_type or "неопределена референтна стойност")
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label")
    population = rv.get("population_label")
    basis = (rv.get("chemical_basis") or {}).get("basis")
    n = len(rv["asserted_in"])
    citation = _claim_citation(rv)

    if rv.get("feed_or_livestock_context"):
        lead = f"Оценка за фуражна добавка (не за храна за хора) определя {type_label}"
        if magnitude and unit:
            lead += f" от {magnitude} {unit}"
        if population:
            lead += f" за популация, описана като {population!r}"
        lead += "."
        tail = " Това е констатация в контекст на фураж/условия на труд и не е приложима като насока за хранене на хора."
    elif magnitude and unit and population:
        basis_phrase = f", с химична основа {basis}" if basis else " (точната химична основа на стойността не е установена от текста на източника)"
        lead = f"ЕФСА определя {type_label} от {magnitude} {unit} за общата популация от потребители{basis_phrase}."
        tail = f" Потвърдено в {n} отделни оценки на ЕФСА." if n > 1 else ""
    elif not magnitude:
        lead = f"Становището на ЕФСА относно {type_label} не посочва конкретна числена граница за тази идентичност в извлечения текст."
        tail = " Вижте вътрешния цитат по-долу за точната формулировка на източника."
    else:
        lead = f"ЕФСА е регистрирала {type_label}, като някои полета не са установени от текста на източника."
        tail = ""
    return f"{lead}{tail} (Източник: {citation}.)"


def _internal_evidence_entry(rv: dict) -> str:
    """The verbatim source quote this claim's paraphrase is built from,
    kept in its own section and never presented as a BG description nor
    merged into the paraphrase itself."""
    quote = rv.get("justification_and_comments") or "(no justification text extracted)"
    citation = _claim_citation(rv)
    return f"[{rv.get('value_type')}, {citation}] “{quote}”"


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

    bundles = build_dossier_bundles(match_result.exact_dossiers, dossiers_dir, codebook)
    ok_bundles = [b for b in bundles if b.status == "ok"]
    extraction_errors = [b for b in bundles if b.status != "ok"]

    deduped_values = _dedupe_reference_values(ok_bundles)
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

    # Fields the presentation spec's "What it is" / "Purpose in food"
    # sections would need: this dataset's own reference values and
    # endpoint summaries never carry plain-language identity/origin or
    # technological-function text, so those two sections are never
    # fabricated -- omitted from both drafts, with the omission recorded
    # here (internal-only; task: "retain omitted-field notes internally"
    # even when the empty section itself is hidden from the drafts).
    omitted_sections = [
        {
            "section": "What it is",
            "reason": "no plain-language identity/origin text is extracted from OpenFoodTox reference values or endpoints",
        },
        {
            "section": "Purpose in food",
            "reason": "no sourced technological-function text is extracted from OpenFoodTox for this identity",
        },
        {
            "section": "Intake guidance",
            "reason": (
                "gated on scientific review (statuses.scientific_review); every profile in this pilot is "
                "deliberately not_reviewed, so no numeric guidance section is ever shown, even for a "
                "consumer_guidance_eligible reference value"
            ),
        },
    ]

    en_sections: list[str] = []
    bg_sections: list[str] = []
    internal_evidence: list[str] = []
    if deduped_values:
        en_sections.append(
            "## Effects and conditions\n\n" + "\n\n".join(_paraphrase_reference_value_en(v) for v in deduped_values)
        )
        bg_sections.append(
            "## Ефекти и условия\n\n" + "\n\n".join(_paraphrase_reference_value_bg(v) for v in deduped_values)
        )
        internal_evidence = [_internal_evidence_entry(v) for v in deduped_values]
    else:
        en_sections.append(
            "## Effects and conditions\n\n(No reference-value evidence was extracted for this identity in the matched dossiers; hidden rather than shown empty.)"
        )
        bg_sections.append(
            "## Ефекти и условия\n\n(За тази идентичност не са извлечени референтни стойности от съпоставените досиета.)"
        )

    sources_en = ["## Sources"]
    sources_bg = ["## Източници"]
    for b in ok_bundles:
        cite = f"- {b.title} ({b.date_of_evaluation or 'date not extracted'})"
        if b.persistent_identifier:
            cite += f" -- {b.persistent_identifier}"
        sources_en.append(cite)
        sources_bg.append(cite)  # citations are not translated (titles/DOIs stay as published)
    en_sections.append("\n".join(sources_en))
    bg_sections.append("\n".join(sources_bg))

    header_en = f"# {catalogue_identity.common_name} ({catalogue_identity.e_number_raw})\n\nStatus: DRAFT -- not scientifically reviewed, not translation-reviewed, not publication-ready."
    header_bg = f"# {catalogue_identity.common_name} ({catalogue_identity.e_number_raw})\n\nСтатус: ЧЕРНОВА -- не е научно прегледано, не е прегледан преводът, не е готово за публикуване."

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
        "human_health_endpoint_count": human_health_total,
        "omitted_sections_internal_note": omitted_sections,
        "statuses": statuses,
        "outcome": outcome,
        "draft_en": header_en + "\n\n" + "\n\n".join(en_sections),
        "draft_bg": header_bg + "\n\n" + "\n\n".join(bg_sections),
        "internal_evidence_en": "## Internal evidence (verbatim source quotations -- not for direct publication)\n\n"
        + ("\n\n".join(internal_evidence) if internal_evidence else "(none extracted)"),
    }


def _format_reference_value_bg(rv: dict) -> str:
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label") or "(единицата не е установена)"
    population = rv.get("population_label") or "(популацията не е установена)"
    basis = (rv.get("chemical_basis") or {}).get("basis")
    dates = sorted({a["date_of_evaluation"] for a in rv["asserted_in"] if a.get("date_of_evaluation")})
    date_note = f" (дата(и) на оценка: {', '.join(dates)})" if dates else " (датата на оценката не е извлечена)"
    n = len(rv["asserted_in"])
    reaffirmed_note = f" Потвърдено в {n} отделни оценки на ЕФСА." if n > 1 else ""
    basis_note = (
        f" Химична основа: {basis}." if basis else " Химичната основа не е установена от текста -- не е одобрено за числени насоки към потребителя в този вид."
    )
    feed_note = (
        " ЗАБЕЛЕЖКА: тази референтна стойност произлиза от оценка за фуражна добавка (FEEDAP), а не от оценка за храна за хора, и не трябва да се представя като насока за хранене на хора."
        if rv.get("feed_or_livestock_context")
        else ""
    )
    quote = rv.get("justification_and_comments") or "(не е извлечен обосноваващ текст)"
    return (
        f"Референтна стойност ({rv.get('value_type')}): {magnitude or '(не е извлечена)'} {unit}, популация: {population}.{date_note}"
        f"{reaffirmed_note}{basis_note}{feed_note}\n"
        f"Цитат от източника (на английски език, буквален цитат -- не е преведен, за да се избегне промяна на научния/правния смисъл): “{quote}”"
    )
