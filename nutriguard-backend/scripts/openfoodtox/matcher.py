"""Offline, dry-run identifier matching between a NutriGuard catalogue
snapshot (see ``catalogue_snapshot.py``) and the OpenFoodTox staging
catalogue (``scripts/openfoodtox/extract.py``'s ``catalogue.jsonl``),
plus document/subject-link resolution within one matched dossier.

Matching rules (see docs/OPENFOODTOX_PILOT_TASK.md "Matching rules"):

- Only exact, normalized, explicitly sourced CAS and E-number
  identifiers are compared. No fuzzy names, no AI identity guesses, no
  assignment by list position.
- When both a catalogue identity and a dossier identity carry *both*
  identifier types, both must agree -- a single matching identifier
  does not override a conflicting one.
- A missing identifier on either side is never treated as evidence of
  equivalence (it just means that identifier type cannot be compared).
- A dossier's own REFERENCE_SUBSTANCE record with more than one
  distinct recognized E-number candidate is not usable for matching at
  all (its own identity is ambiguous first).
- An E-number "range" candidate (e.g. ``E251-252``) can never become an
  exact single-substance link, even if the catalogue's bare code falls
  inside it.
- Multiple dossiers resolving to the *same* underlying substance
  identity (same CAS, or -- when CAS is absent on the dossier side too
  -- the same reference-substance name) are multiple assessments of
  one substance, not duplicates and not an ambiguity.
- Dossiers whose matched identifier(s) resolve to *different*
  underlying substance identities are reported as ambiguous, never
  silently merged or arbitrarily picked.

This module never fetches anything over the network and never opens a
database connection; it only reads the already-extracted, locally
staged ``catalogue.jsonl`` (streamed line by line -- never loaded
whole into memory) and, for subject-link resolution, an in-memory
dossier record already produced by ``dossier.build_dossier_record``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .catalogue_snapshot import CatalogueIdentity


@dataclass
class DossierHit:
    dossier_file: str
    title: str | None
    date_of_evaluation: str | None
    rs_document_key: str | None
    rs_name: str | None
    rs_cas_raw: str | None
    rs_cas_normalized: str | None
    rs_e_numbers_normalized: list[str]
    rs_e_number_conflict: bool
    verdict: str  # "exact" | "conflicting" | "ambiguous_range" | "source_identity_ambiguous"
    explanation: str
    resolved_identity_key: str | None  # grouping key used to detect "same substance, multiple assessments"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier_file": self.dossier_file,
            "title": self.title,
            "date_of_evaluation": self.date_of_evaluation,
            "reference_substance_document_key": self.rs_document_key,
            "reference_substance_name": self.rs_name,
            "reference_substance_cas_raw": self.rs_cas_raw,
            "reference_substance_cas_normalized": self.rs_cas_normalized,
            "reference_substance_e_numbers_normalized": self.rs_e_numbers_normalized,
            "reference_substance_e_number_conflict": self.rs_e_number_conflict,
            "verdict": self.verdict,
            "explanation": self.explanation,
        }


@dataclass
class MatchResult:
    catalogue_identity: CatalogueIdentity
    exact_dossiers: list[DossierHit] = field(default_factory=list)
    conflicting_dossiers: list[DossierHit] = field(default_factory=list)
    ambiguous_dossiers: list[DossierHit] = field(default_factory=list)
    overall_status: str = "no_match"  # "exact_match" | "no_match" | "ambiguous" | "conflicting"
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalogue_identity": self.catalogue_identity.to_dict(),
            "overall_status": self.overall_status,
            "explanation": self.explanation,
            "exact_dossiers": [h.to_dict() for h in self.exact_dossiers],
            "conflicting_dossiers": [h.to_dict() for h in self.conflicting_dossiers],
            "ambiguous_dossiers": [h.to_dict() for h in self.ambiguous_dossiers],
        }


def _range_contains(code1: str, code2: str, target_code: str) -> bool:
    try:
        lo, hi, target = int(code1), int(code2), int(target_code)
    except ValueError:
        return False
    return min(lo, hi) <= target <= max(lo, hi)


def _compare_one(cat: CatalogueIdentity, rs: dict) -> DossierHit | None:
    """Compare one catalogue identity against one dossier
    REFERENCE_SUBSTANCE record. Returns ``None`` only when there is
    nothing at all comparable AND nothing worth recording (used to skip
    building a hit for dossiers that are plainly unrelated, keeping the
    streaming scan's memory bounded -- true "no match" results are
    never surfaced per-dossier, only the catalogue-identity-level
    summary says "no_match" when nothing qualified at all).
    """
    e_numbers_info = rs.get("e_numbers") or {}
    conflict = bool(e_numbers_info.get("conflict"))
    recognized_singles = sorted(
        {
            c["normalized"]
            for c in e_numbers_info.get("candidates", [])
            if c.get("recognized") and c.get("kind") == "single"
        }
    )
    range_candidates = [c for c in e_numbers_info.get("candidates", []) if c.get("recognized") and c.get("kind") == "range"]

    cas_c = cat.cas_number_normalized
    enum_c = cat.e_number_normalized

    def _hit(verdict: str, explanation: str, resolved_key: str | None) -> DossierHit:
        return DossierHit(
            dossier_file=rs.get("_dossier_file"),
            title=rs.get("_dossier_title"),
            date_of_evaluation=rs.get("_date_of_evaluation"),
            rs_document_key=rs.get("document_key"),
            rs_name=rs.get("name"),
            rs_cas_raw=rs.get("cas_number"),
            rs_cas_normalized=_normalize_cas_local(rs.get("cas_number")),
            rs_e_numbers_normalized=recognized_singles,
            rs_e_number_conflict=conflict,
            verdict=verdict,
            explanation=explanation,
            resolved_identity_key=resolved_key,
        )

    if conflict:
        if cas_c is None and enum_c is None:
            return None
        # Only surface this as a hit if the catalogue identity otherwise
        # looks relevant (shares the CAS, or the conflicted E-number set
        # contains the queried code) -- otherwise it's just unrelated noise.
        if cas_c is not None and _normalize_cas_local(rs.get("cas_number")) == cas_c:
            return _hit(
                "source_identity_ambiguous",
                "CAS matches, but this dossier's own REFERENCE_SUBSTANCE record has multiple "
                "distinct recognized E-number candidates on one record -- its own identity is "
                "ambiguous and is not used for matching without manual review.",
                None,
            )
        if enum_c is not None and enum_c in recognized_singles:
            return _hit(
                "source_identity_ambiguous",
                "E-number matches, but this dossier's own REFERENCE_SUBSTANCE record has multiple "
                "distinct recognized E-number candidates on one record -- its own identity is "
                "ambiguous and is not used for matching without manual review.",
                None,
            )
        return None

    cas_d = _normalize_cas_local(rs.get("cas_number"))
    enum_d = recognized_singles[0] if len(recognized_singles) == 1 else None

    cas_present_both = cas_c is not None and cas_d is not None
    enum_present_both = enum_c is not None and enum_d is not None
    cas_agree = cas_present_both and cas_c == cas_d
    enum_agree = enum_present_both and enum_c == enum_d

    if cas_present_both and enum_present_both:
        if cas_agree and enum_agree:
            return _hit("exact", "CAS and E-number are both explicitly present on both sides and agree.", cas_d)
        return _hit(
            "conflicting",
            f"CAS and E-number are both explicitly present but disagree "
            f"(CAS: catalogue={cas_c!r} vs dataset={cas_d!r}; "
            f"E-number: catalogue={enum_c!r} vs dataset={enum_d!r}). Unresolved even though one matched.",
            None,
        )
    # Only one identifier *type* is comparable at all here (the other is
    # missing on at least one side). A single differing identifier with
    # nothing else to corroborate a relationship is simply a different,
    # unrelated substance -- not a "conflict" needing manual review (that
    # label is reserved for when both types are explicitly present and
    # disagree, per the task's own "even if one matches" framing, which
    # presupposes both were comparable in the first place). Falling
    # through here means an unrelated dossier yields no hit at all,
    # keeping the streaming scan's memory bounded by actual matches.
    if cas_agree:
        return _hit(
            "exact",
            "Matched by CAS number alone (E-number not comparable on both sides -- "
            "its absence is not evidence either way).",
            cas_d,
        )
    if enum_agree:
        return _hit(
            "exact",
            "Matched by E-number alone (CAS not comparable on both sides -- "
            "its absence is not evidence either way).",
            cas_d if cas_d else (rs.get("name") or "").strip().lower() or None,
        )
    if enum_c is not None and not recognized_singles:
        for rc in range_candidates:
            codes = rc.get("codes") or []
            if len(codes) == 2:
                base_code = "".join(ch for ch in enum_c if ch.isdigit())
                if _range_contains(codes[0], codes[1], base_code):
                    return _hit(
                        "ambiguous_range",
                        f"Dataset only has an explicit range candidate ({rc.get('normalized')}) covering the "
                        f"queried E-number's numeric code -- a range cannot become an exact single-substance "
                        f"link, so this is reported as ambiguous, not matched.",
                        None,
                    )
    return None


def _normalize_cas_local(raw: str | None) -> str | None:
    from .catalogue_snapshot import normalize_cas

    return normalize_cas(raw)


def match_catalogue_against_staging(
    catalogue_entries: Iterable[CatalogueIdentity], catalogue_jsonl_path: str
) -> list[MatchResult]:
    """Stream ``catalogue_jsonl_path`` once, comparing every
    REFERENCE_SUBSTANCE identity in it against every supplied catalogue
    entry. Memory use is bounded by the number of catalogue entries and
    the number of *hits* found (expected small for a pilot's handful of
    substances), never by the size of the staged dataset itself.
    """
    entries = list(catalogue_entries)
    hits_by_entry: dict[str, list[DossierHit]] = {c.id: [] for c in entries}

    with open(catalogue_jsonl_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("status") != "ok":
                continue
            dossier_summary = record.get("dossier_summary") or {}
            ref_subs = record.get("identity", {}).get("reference_substances", [])
            for rs in ref_subs:
                rs = {
                    **rs,
                    "_dossier_file": record["dossier_file"],
                    "_dossier_title": record.get("title"),
                    "_date_of_evaluation": dossier_summary.get("date_of_evaluation"),
                }
                for cat in entries:
                    hit = _compare_one(cat, rs)
                    if hit is not None:
                        hits_by_entry[cat.id].append(hit)

    results: list[MatchResult] = []
    for cat in entries:
        hits = hits_by_entry[cat.id]
        exact = [h for h in hits if h.verdict == "exact"]
        conflicting = [h for h in hits if h.verdict in ("conflicting", "source_identity_ambiguous")]
        ambiguous = [h for h in hits if h.verdict == "ambiguous_range"]

        distinct_keys = {h.resolved_identity_key for h in exact if h.resolved_identity_key is not None}
        if len(distinct_keys) > 1:
            # Exact per-pair matches that disagree with each other on the
            # underlying substance identity (e.g. the catalogue's lone
            # E-number resolved to reference substances with two
            # different CAS numbers across dossiers) -- never silently
            # pick one; move them all to ambiguous.
            ambiguous = ambiguous + exact
            exact = []

        result = MatchResult(
            catalogue_identity=cat,
            exact_dossiers=exact,
            conflicting_dossiers=conflicting,
            ambiguous_dossiers=ambiguous,
            overall_status="no_match",
            explanation="",
        )
        if exact:
            n = len(exact)
            result.overall_status = "exact_match"
            result.explanation = (
                f"{n} dossier(s) resolve to one consistent substance identity."
                + (f" {len(conflicting)} other dossier(s) with a conflicting/ambiguous identifier were "
                   f"excluded, not merged -- see conflicting_dossiers." if conflicting else "")
            )
        elif ambiguous:
            result.overall_status = "ambiguous"
            result.explanation = (
                "Matched identifier(s) resolve to more than one distinct underlying substance identity, "
                "or only to an unresolved range candidate -- cannot be linked to one specific substance."
            )
        elif conflicting:
            result.overall_status = "conflicting"
            result.explanation = (
                "Every dossier that shared an identifier with this catalogue entry had another "
                "identifier that disagreed, or its own identity was itself ambiguous -- unresolved."
            )
        else:
            result.overall_status = "no_match"
            result.explanation = (
                "No dossier in the staged dataset shares an explicit, normalized CAS or E-number "
                "identifier with this catalogue entry (a missing identifier on either side is not "
                "treated as evidence of equivalence)."
            )
        results.append(result)

    return results


def resolve_subject_links(dossier_record: dict) -> dict[str, Any]:
    """Resolve which REFERENCE_SUBSTANCE identity each reference-value
    and endpoint record in one fully parsed dossier actually belongs
    to, instead of assuming every item in the archive belongs to every
    identity (or to "the" identity, singular) -- most dossiers (~97% of
    this dataset) have exactly one REFERENCE_SUBSTANCE and the
    assumption is safe there, but ~3% have more than one (e.g. a
    pesticide's metabolites, each with their own CAS), where it is not.

    Resolution path for a dossier with more than one REFERENCE_SUBSTANCE:
    document.manifest_links -> a "CHILD" (or any) link whose ref_uuid is
    either a REFERENCE_SUBSTANCE's own document_key, or a SUBSTANCE
    document's document_key (itself resolved to the REFERENCE_SUBSTANCE
    it names via SUBSTANCE.derived.reference_substance_ref). An item
    with no resolvable link, or with links resolving to more than one
    distinct identity, is quarantined under ``unresolved`` rather than
    attached anywhere.
    """
    ref_subs = dossier_record.get("identity", {}).get("reference_substances", [])
    substances = dossier_record.get("identity", {}).get("substances", [])

    ref_sub_keys = {rs["document_key"] for rs in ref_subs if rs.get("document_key")}
    substance_to_refsub: dict[str, str] = {}
    for s in substances:
        sub_key = s.get("document_key")
        ref_key = s.get("reference_substance_ref")
        if sub_key and ref_key:
            substance_to_refsub[sub_key] = ref_key

    by_identity: dict[str, dict[str, Any]] = {
        key: {"reference_values": [], "endpoints": {d: [] for d in dossier_record.get("endpoints", {})}}
        for key in ref_sub_keys
    }
    unresolved: dict[str, list[dict]] = {"reference_values": [], "endpoints": []}

    single_identity_key = next(iter(ref_sub_keys)) if len(ref_sub_keys) == 1 else None

    def resolve_item(item: dict) -> tuple[str | None, str]:
        if single_identity_key is not None:
            return single_identity_key, "single_identity_dossier"
        candidate_keys: set[str] = set()
        for link in item.get("manifest_links") or []:
            ref_uuid = link.get("ref_uuid")
            if not ref_uuid:
                continue
            if ref_uuid in ref_sub_keys:
                candidate_keys.add(ref_uuid)
            elif ref_uuid in substance_to_refsub:
                candidate_keys.add(substance_to_refsub[ref_uuid])
        if len(candidate_keys) == 1:
            return next(iter(candidate_keys)), "manifest_child_link"
        if not candidate_keys:
            return None, "no_resolvable_subject_link"
        return None, "multiple_distinct_linked_identities"

    for rv in dossier_record.get("reference_values", []):
        key, basis = resolve_item(rv)
        annotated = {**rv, "subject_linkage_basis": basis}
        if key is not None:
            by_identity[key]["reference_values"].append(annotated)
        else:
            unresolved["reference_values"].append(annotated)

    for domain, items in dossier_record.get("endpoints", {}).items():
        for ep in items:
            key, basis = resolve_item(ep)
            annotated = {**ep, "domain": domain, "subject_linkage_basis": basis}
            if key is not None:
                by_identity[key]["endpoints"].setdefault(domain, []).append(annotated)
            else:
                unresolved["endpoints"].append(annotated)

    return {"by_identity": by_identity, "unresolved": unresolved}
