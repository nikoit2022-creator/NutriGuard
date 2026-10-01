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
honest "not yet done" value. The draft EN/BG text only ever restates
already-extracted facts (quoting the source's own justification text
verbatim rather than paraphrasing it, to avoid subtly changing a
scientific/legal source's meaning) plus structural connective text
this module writes itself -- never a fabricated claim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .catalogue_snapshot import CatalogueIdentity
from .codebook import Codebook
from .dossier import build_dossier_record
from .matcher import DossierHit, MatchResult, resolve_subject_links

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
        reference_values = [
            {**rv, "feed_or_livestock_context": feed_context, "review_eligible": _review_eligible(rv, feed_context)}
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


def _review_eligible(rv: dict, feed_context: bool) -> bool:
    if feed_context:
        return False
    basis = (rv.get("chemical_basis") or {}).get("status")
    return basis == "resolved" and bool(rv.get("population_label")) and bool(rv.get("unit_label"))


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


def _format_reference_value_en(rv: dict) -> str:
    magnitude = rv.get("value") or rv.get("lower_value") or rv.get("upper_value")
    unit = rv.get("unit_label") or "(unit not resolved)"
    population = rv.get("population_label") or "(population not resolved)"
    basis = (rv.get("chemical_basis") or {}).get("basis")
    dates = sorted({a["date_of_evaluation"] for a in rv["asserted_in"] if a.get("date_of_evaluation")})
    date_note = f" (assessment date(s): {', '.join(dates)})" if dates else " (assessment date not extracted)"
    n = len(rv["asserted_in"])
    reaffirmed_note = f" Reaffirmed across {n} separate EFSA assessments." if n > 1 else ""
    basis_note = f" Chemical basis: {basis}." if basis else " Chemical basis not resolved from the source text -- not approved for numeric consumer guidance as-is."
    feed_note = (
        " NOTE: this reference value comes from an animal-feed (FEEDAP) assessment, not a human food evaluation, "
        "and must not be presented as human dietary guidance."
        if rv.get("feed_or_livestock_context")
        else ""
    )
    quote = rv.get("justification_and_comments") or "(no justification text extracted)"
    return (
        f"{rv.get('value_type')} reference value: {magnitude or '(not extracted)'} {unit}, population: {population}.{date_note}"
        f"{reaffirmed_note}{basis_note}{feed_note}\nSource text (quoted verbatim, not paraphrased): “{quote}”"
    )


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
    any_feed_only = bool(deduped_values) and all(v.get("feed_or_livestock_context") for v in deduped_values)

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

    en_sections: list[str] = []
    bg_sections: list[str] = []
    if deduped_values:
        en_sections.append("## Effects and conditions\n\n" + "\n\n".join(_format_reference_value_en(v) for v in deduped_values))
        bg_sections.append(
            "## Ефекти и условия\n\n"
            + "\n\n".join(_format_reference_value_bg(v) for v in deduped_values)
        )
    else:
        en_sections.append("## Effects and conditions\n\n(No reference-value evidence was extracted for this identity in the matched dossiers; hidden rather than shown empty.)")
        bg_sections.append("## Ефекти и условия\n\n(За тази идентичност не са извлечени референтни стойности от съпоставените досиета.)")

    # "Intake guidance" is deliberately never populated in this pilot:
    # the presentation spec gates it on "only reviewed applicable
    # guidance", and nothing here has gone through scientific review
    # (see statuses.scientific_review above) -- so the section is
    # omitted from the draft entirely rather than shown with an
    # unreviewed number in it.

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
        "statuses": statuses,
        "outcome": outcome,
        "draft_en": header_en + "\n\n" + "\n\n".join(en_sections),
        "draft_bg": header_bg + "\n\n" + "\n\n".join(bg_sections),
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
