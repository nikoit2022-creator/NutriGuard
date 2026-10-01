"""Parse IUCLID ``manifest.xml`` and ``*.i6d`` documents into plain dicts.

Two layers of extraction are produced for every ``.i6d`` document:

1. ``raw_fields`` — a generic, bounded walk of every leaf element in
   the document's ``<i6c:Content>`` payload, each tagged with its full
   element-name path (e.g.
   ``ENDPOINT_STUDY_RECORD.RepeatedDoseToxicityOther/ResultsAndDiscussion/
   EffectLevels/Efflevel/EffectLevel/lowerValue``) and any
   ``i6:uuid``/``i6:key`` attributes on that element. This guarantees
   every field actually present in the source is captured somewhere,
   with its exact source path, even for the many endpoint subtypes
   this module does not hand-model individually.
2. A small set of *derived*, subtype-aware convenience fields (substance
   identity, literature citation, reference values, endpoint key
   findings) layered on top for the record types this audit's scope
   specifically calls out. These are built from the same content and
   never invent a value that is not present in ``raw_fields``.

Every derived and raw field carries a ``source`` provenance dict with
the originating ``archive`` (relative path), the ``entry`` (zip member
name), and the ``document_key``, so any catalogue row can be traced
back to the exact byte range it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import safe_io
from .codebook import Codebook
from .domains import classify
from .chemical_basis import extract_chemical_basis
from .e_numbers import collect_e_numbers

MANIFEST_NS = "http://iuclid6.echa.europa.eu/namespaces/manifest/v1"
XLINK_NS = "http://www.w3.org/1999/xlink"
I6_FIELDS_NS = "http://iuclid6.echa.europa.eu/namespaces/platform-fields/v1"

# Bump this integer whenever a `derive_*` function's output *meaning*
# changes (a field starts/stops being populated, what it represents
# changes, a new quarantine condition is added) -- not for unrelated
# code changes elsewhere in this package. It is a coarse, human-readable
# marker for "is catalogue.jsonl X comparable to catalogue.jsonl Y",
# never a substitute for the exact producing git SHA (and, if the
# working tree was dirty when it ran, a content fingerprint of that
# diff) that every caller must also record alongside it -- see
# extract.py's cmd_catalogue and pilot.py's run_pilot.
#
# History (bump points, most recent first):
#   2 -- 2026-10-01: fixed derive_reference_values capturing a sibling
#        AssessmentBody classification code as the reference value's own
#        numeric magnitude (see docs/OPENFOODTOX_PILOT_REPORT.md).
#   1 -- first value assigned at the point this version marker was
#        introduced; prior catalogue.jsonl output (this dataset's
#        staging/v2, staging/v3) predates the concept entirely and was
#        never tagged with a version -- it must be identified by its own
#        recorded producing git SHA instead, not retroactively assumed
#        to be "version 1".
EXTRACTION_LOGIC_VERSION = 2

# Coverage target: a full, unbounded leaf-count survey of every document
# in the transferred dataset (221,377 documents) found a true maximum of
# 1,405 informative leaves in a single document (an
# ENDPOINT_STUDY_RECORD.BiodegradationInSoil record); 467 documents
# (0.2%) exceeded the *old* 400-leaf cap, all of them environmental/
# physicochemical, none human-health/identity/reference-value. This cap
# is set with a ~2.8x margin over that measured maximum so no real
# document in this dataset is truncated by it. It is a *coverage*
# threshold, not the memory-safety backstop (see _SAFETY_CEILING_LEAVES
# below) -- raising it alone would not by itself prove nothing is
# dropped, which is why truncation detection (see parse_i6d) measures
# the true leaf count unconditionally rather than inferring truncation
# from whether a running counter happened to hit this number.
MAX_RAW_FIELDS_PER_DOCUMENT = 4000

# Hard memory-safety ceiling, independent of the coverage target above:
# bounds worst-case memory from a single pathological/adversarial
# document built from many small same-tag leaf elements (a resource-
# exhaustion shape distinct from the entity-expansion attacks safe_io.py
# already blocks, and not fully covered by safe_io's byte-size caps,
# since many short elements can fit in a modest byte budget). Walking
# never collects more than this many leaves, no matter how large
# MAX_RAW_FIELDS_PER_DOCUMENT is set to.
_SAFETY_CEILING_LEAVES = 50_000


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _text(elem) -> str | None:
    if elem is None or elem.text is None:
        return None
    t = elem.text.strip()
    return t or None


# ---------------------------------------------------------------- manifest --

def parse_manifest(xml_bytes: bytes) -> dict[str, Any]:
    root = safe_io.parse_safe_xml(xml_bytes)
    gi = root.find(_q(MANIFEST_NS, "general-information"))
    general = {}
    if gi is not None:
        general = {
            "title": _text(gi.find(_q(MANIFEST_NS, "title"))),
            "created": _text(gi.find(_q(MANIFEST_NS, "created"))),
            "application": _text(gi.find(_q(MANIFEST_NS, "application"))),
            "submission_type": _text(gi.find(_q(MANIFEST_NS, "submission-type"))),
            "archive_type": _text(gi.find(_q(MANIFEST_NS, "archive-type"))),
            "legislations": [
                {
                    "id": _text(leg.find(_q(MANIFEST_NS, "id"))),
                    "version": _text(leg.find(_q(MANIFEST_NS, "version"))),
                }
                for leg in root.findall(
                    f"{_q(MANIFEST_NS, 'general-information')}/"
                    f"{_q(MANIFEST_NS, 'legislations-info')}/{_q(MANIFEST_NS, 'legislation')}"
                )
            ],
        }

    documents = []
    cd = root.find(_q(MANIFEST_NS, "contained-documents"))
    if cd is not None:
        for d in cd.findall(_q(MANIFEST_NS, "document")):
            name_el = d.find(_q(MANIFEST_NS, "name"))
            href = name_el.get(_q(XLINK_NS, "href")) if name_el is not None else None
            links = [
                {
                    "ref_uuid": _text(link.find(_q(MANIFEST_NS, "ref-uuid"))),
                    "ref_type": _text(link.find(_q(MANIFEST_NS, "ref-type"))),
                }
                for link in d.findall(f"{_q(MANIFEST_NS, 'links')}/{_q(MANIFEST_NS, 'link')}")
            ]
            documents.append(
                {
                    "id": d.get("id"),
                    "type": _text(d.find(_q(MANIFEST_NS, "type"))),
                    "subtype": _text(d.find(_q(MANIFEST_NS, "subtype"))),
                    "name": _text(name_el),
                    "entry": href,
                    "uuid": _text(d.find(_q(MANIFEST_NS, "uuid"))),
                    "first_modification_date": _text(d.find(_q(MANIFEST_NS, "first-modification-date"))),
                    "last_modification_date": _text(d.find(_q(MANIFEST_NS, "last-modification-date"))),
                    "links": links,
                }
            )
    base_doc_uuid = _text(root.find(_q(MANIFEST_NS, "base-document-uuid")))
    return {"general": general, "base_document_uuid": base_doc_uuid, "documents": documents}


# --------------------------------------------------------------------- i6d --

PLATFORM_META_NS = "http://iuclid6.echa.europa.eu/namespaces/platform-metadata/v1"
PLATFORM_CONTAINER_NS = "http://iuclid6.echa.europa.eu/namespaces/platform-container/v2"


def _walk_leaves(elem, path: list[str], out: list[dict], ceiling: list[int], entry_uuid: str | None = None) -> None:
    """Flatten ``elem`` into leaf (path, text) records.

    IUCLID puts the ``i6:uuid`` that identifies a repeatable entry
    (e.g. ``Efflevel/entry``, ``Synonyms/entry``) on the *wrapper*
    element, which itself has children and is therefore never a leaf.
    We propagate the nearest enclosing such uuid down to every leaf
    below it as ``entry_uuid`` so callers can group repeated entries
    correctly without depending on document order alone.

    ``ceiling`` is the hard memory-safety stop
    (``_SAFETY_CEILING_LEAVES``), not the coverage target
    (``MAX_RAW_FIELDS_PER_DOCUMENT``) -- this function always walks the
    *entire* tree (up to that hard ceiling) so the caller can compare
    the true leaf count against the coverage target and know for
    certain whether anything was actually left out, rather than
    inferring it from a counter that stopped early.
    """
    if ceiling[0] <= 0:
        return
    tag = _local(elem.tag)
    new_path = path + [tag]
    own_uuid = None
    for k, v in elem.attrib.items():
        if _local(k) == "uuid":
            own_uuid = v
            break
    effective_uuid = own_uuid or entry_uuid
    children = list(elem)
    if not children:
        text = _text(elem)
        attrs = {_local(k): v for k, v in elem.attrib.items() if _local(k) in ("uuid", "key")}
        if text or attrs or effective_uuid:
            row: dict = {"path": "/".join(new_path), "text": text, "attrs": attrs or None}
            if effective_uuid:
                row["entry_uuid"] = effective_uuid
            out.append(row)
            ceiling[0] -= 1
        return
    for child in children:
        _walk_leaves(child, new_path, out, ceiling, entry_uuid=effective_uuid)
        if ceiling[0] <= 0:
            break


def parse_i6d(xml_bytes: bytes) -> dict[str, Any]:
    root = safe_io.parse_safe_xml(xml_bytes)
    meta_el = root.find(f"{_q(PLATFORM_CONTAINER_NS, 'PlatformMetadata')}")
    meta = {}
    if meta_el is not None:
        for key in (
            "documentKey",
            "parentDocumentKey",
            "name",
            "documentType",
            "documentSubType",
            "definitionVersion",
            "creationDate",
            "lastModificationDate",
            "submissionType",
        ):
            meta[key] = _text(meta_el.find(_q(PLATFORM_META_NS, key)))

    content_el = root.find(_q(PLATFORM_CONTAINER_NS, "Content"))
    raw_fields: list[dict] = []
    truncated = False
    safety_ceiling_hit = False
    tag_counts: dict[str, int] = {}
    if content_el is not None and len(content_el):
        payload = list(content_el)[0]  # the single <DOCUMENTTYPE ...> element
        full_leaves: list[dict] = []
        ceiling = [_SAFETY_CEILING_LEAVES]
        _walk_leaves(payload, [], full_leaves, ceiling)
        safety_ceiling_hit = ceiling[0] <= 0
        true_count = len(full_leaves)
        if true_count > MAX_RAW_FIELDS_PER_DOCUMENT:
            raw_fields = full_leaves[:MAX_RAW_FIELDS_PER_DOCUMENT]
            truncated = True
        else:
            raw_fields = full_leaves
            truncated = False
        for e in payload.iter():
            t = _local(e.tag)
            tag_counts[t] = tag_counts.get(t, 0) + 1

    return {
        "metadata": meta,
        "raw_fields": raw_fields,
        "raw_fields_truncated": truncated,
        "raw_fields_safety_ceiling_hit": safety_ceiling_hit,
        "tag_counts": tag_counts,
    }


# ------------------------------------------------------------ derived views --

def _fields_index(raw_fields: list[dict]) -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for f in raw_fields:
        idx.setdefault(f["path"], []).append(f)
    return idx


def _first_text(idx: dict[str, list[dict]], suffix: str) -> str | None:
    for path, entries in idx.items():
        if path.endswith(suffix):
            return entries[0]["text"]
    return None


def derive_substance(raw_fields: list[dict]) -> dict:
    idx = _fields_index(raw_fields)
    return {
        "chemical_name": _first_text(idx, "ChemicalName"),
        "owner_legal_entity_ref": _first_text(idx, "OwnerLegalEntity"),
        "reference_substance_ref": _first_text(idx, "ReferenceSubstance/ReferenceSubstance"),
        "type_of_substance_code": _first_text(idx, "TypeOfSubstance/Composition/value"),
    }


def derive_reference_substance(raw_fields: list[dict]) -> dict:
    idx = _fields_index(raw_fields)
    cas = _first_text(idx, "CASNumber")
    ec = None
    ec_code = None
    for path, entries in idx.items():
        if path.endswith("InventoryEntry/entry/i6:numberInInventory") or path.endswith(
            "InventoryEntry/entry/numberInInventory"
        ):
            ec = entries[0]["text"]
        if path.endswith("InventoryEntry/entry/i6:inventoryCode") or path.endswith(
            "InventoryEntry/entry/inventoryCode"
        ):
            ec_code = entries[0]["text"]
    synonyms = []
    for path, entries in idx.items():
        if path.endswith("Synonyms/Synonyms/entry/Name"):
            for e in entries:
                if e["text"]:
                    synonyms.append(e["text"])
    e_numbers = collect_e_numbers(synonyms)
    return {
        "name": _first_text(idx, "ReferenceSubstanceName"),
        "iupac_name": _first_text(idx, "IupacName"),
        "cas_number": cas or None,
        "ec_number": ec if ec_code == "EC" else None,
        "synonyms": synonyms,
        # Structured, conflict-aware E-number recognition (see
        # e_numbers.py). A record can have zero, one, or (if the data
        # ever warrants it) multiple distinct recognized candidates --
        # never collapsed to a single guessed scalar.
        "e_numbers": e_numbers,
        "molecular_formula": _first_text(idx, "MolecularFormula"),
        "smiles": _first_text(idx, "SmilesNotation"),
        "inchi": _first_text(idx, "InChl"),
    }


def derive_legal_entity(raw_fields: list[dict]) -> dict:
    idx = _fields_index(raw_fields)
    return {
        "name": _first_text(idx, "GeneralInfo/LegalEntityName"),
        "legal_entity_type_code": _first_text(idx, "GeneralInfo/LegalEntityType/value"),
    }


def derive_literature(raw_fields: list[dict]) -> dict:
    idx = _fields_index(raw_fields)
    lit_type_other = None
    lit_type_code = None
    for path, entries in idx.items():
        if path.endswith("LiteratureType/other"):
            lit_type_other = entries[0]["text"]
        if path.endswith("LiteratureType/value"):
            lit_type_code = entries[0]["text"]
    return {
        "name": _first_text(idx, "GeneralInfo/Name"),
        "author": _first_text(idx, "GeneralInfo/Author"),
        "reference_year": _first_text(idx, "GeneralInfo/ReferenceYear"),
        # Named "citation_source" (not "source") to avoid colliding with the
        # provenance envelope's "source" key (archive/entry/document_key)
        # that callers merge alongside every derived dict.
        "citation_source": _first_text(idx, "GeneralInfo/Source"),
        "remarks": _first_text(idx, "GeneralInfo/Remarks"),
        "literature_type_code": lit_type_code,
        "literature_type_label": lit_type_other,
    }


def derive_dossier(raw_fields: list[dict]) -> dict:
    idx = _fields_index(raw_fields)
    return {
        "output_title": _first_text(idx, "LiteratureReference/EFSAOutputTitle"),
        "date_of_evaluation": _first_text(idx, "LiteratureReference/DateOfEvaluation"),
        "persistent_identifier": _first_text(idx, "LiteratureReference/LinkToPersistentIdentifier"),
        "efsa_question_number": _first_text(idx, "DataSource/EFSAQuestionNumber"),
        "remarks": _first_text(idx, "remarks"),
        "dossier_subject_ref": _first_text(idx, "DossierSubject/Name"),
        "submitting_legal_entity_ref": _first_text(idx, "SubmittingLegalEntity")
        or _first_text(idx, "DossierSubject/SubmittingLegalEntity"),
        "food_domain_code": _first_text(idx, "Domain/FoodDomain/value"),
        "regulation_code": _first_text(idx, "Domain/Regulation/value"),
        "expert_group_code": _first_text(idx, "Domain/ExpertGroup/value"),
    }


_REF_VALUE_CONTAINERS = {
    "AcceptableDailyIntake": "ADI",
    "AcuteReferenceDose": "ARfD",
    "AcceptableOperatorExposureLevel": "AOEL",
    "AcuteAcceptableOperatorExposureLevel": "AAOEL",
    "OtherReferenceValues": "OTHER",
}
_VALUE_LEAF_NAMES = {"value", "lowerValue", "upperValue"}

# Sub-wrapper tags observed, across the full 11,613-dossier dataset, to be
# the sole carriers of a reference value's actual numeric magnitude (see the
# allowlist comment at its use site below). "RefValue" belongs to
# OtherReferenceValues; the other four each belong to the identically-named
# container (Adi -> AcceptableDailyIntake, Arfd -> AcuteReferenceDose, Aoel ->
# AcceptableOperatorExposureLevel, Aaoel -> Ac(ute)AcceptableOperatorExposureLevel).
_VALUE_HOLDING_WRAPPERS = {"Adi", "Arfd", "Aoel", "Aaoel", "RefValue"}


def derive_reference_values(raw_fields: list[dict]) -> list[dict]:
    """Extract ADI/ARfD/AOEL/AAOEL/other reference-value blocks.

    Handles the fact that different IUCLID reference-value containers
    name their value-holding child differently (``Adi``, ``Arfd``,
    ``Aoel``, ``Aaoel``, ``RefValue``) by matching on the *grandchild*
    leaf names (``value``/``lowerValue``/``upperValue``/``unitCode``)
    rather than the container tag name.
    """
    results: list[dict] = []
    for container_tag, value_type in _REF_VALUE_CONTAINERS.items():
        by_path = {}
        for f in raw_fields:
            parts = f["path"].split("/")
            if container_tag not in parts:
                continue
            idx = parts.index(container_tag)
            key_tail = tuple(parts[idx:])
            by_path.setdefault(key_tail[:1], []).append((key_tail, f))
        if not by_path:
            continue
        # group by occurrence: consecutive fields sharing the same container tag
        # index in the flat list form one record when there are multiple.
        group: dict = {}
        for f in raw_fields:
            parts = f["path"].split("/")
            if container_tag not in parts:
                continue
            idx = parts.index(container_tag)
            tail = "/".join(parts[idx + 1 :])
            group.setdefault(True, []).append((tail, f))
        entry: dict[str, Any] = {
            "value_type": value_type,
            "lower_value": None,
            "upper_value": None,
            "value": None,
            "unit_code": None,
            "population_code": None,
            "overall_uncertainty": None,
            "critical_endpoint_ref": None,
            "reference_to_efsa_opinion_ref": None,
            "justification_and_comments": None,
            "reference_value_descriptor_label": None,
        }
        for tail, f in group.get(True, []):
            leaf = tail.split("/")[-1]
            # The numeric magnitude (value/lowerValue/upperValue) of a reference
            # value is only ever carried by one of these named sub-wrappers
            # (confirmed across the full dataset: zero occurrences of an
            # unwrapped "<container>/value"). Several *other* sibling fields --
            # notably "AssessmentBody/value" (a coded classification of which
            # body made the assessment, e.g. "HBGV not from EFSA
            # committees/panels", self-describing via its own "other" leaf) --
            # also happen to end in a leaf literally named "value" and must
            # never be mistaken for the reference value's own magnitude. An
            # explicit allowlist (rather than excluding known non-value
            # wrappers one at a time) is used so an unrecognized wrapper is
            # quarantined (silently not captured as a value) instead of
            # silently misattributed.
            wrapper = tail.split("/")[0]
            if leaf == "lowerValue" and wrapper in _VALUE_HOLDING_WRAPPERS:
                entry["lower_value"] = f["text"]
            elif leaf == "upperValue" and wrapper in _VALUE_HOLDING_WRAPPERS:
                entry["upper_value"] = f["text"]
            elif leaf == "value" and wrapper in _VALUE_HOLDING_WRAPPERS:
                entry["value"] = f["text"]
            elif leaf == "unitCode":
                entry["unit_code"] = f["text"]
            elif tail.startswith("Population") and leaf == "value":
                entry["population_code"] = f["text"]
            elif leaf == "OverallUncertainty":
                entry["overall_uncertainty"] = f["text"]
            elif leaf == "CriticalEndpoint":
                entry["critical_endpoint_ref"] = f["text"]
            elif leaf == "ReferenceToEFSAOpinion" or tail.endswith("ReferenceToEFSAOpinion/key"):
                entry["reference_to_efsa_opinion_ref"] = f["text"]
            elif leaf == "JustificationAndComments":
                entry["justification_and_comments"] = f["text"]
            elif tail.startswith("ReferenceValueDescriptor") and leaf == "other":
                entry["reference_value_descriptor_label"] = f["text"]
        if any(v is not None for k, v in entry.items() if k != "value_type"):
            # Chemical-basis recovery needs the *decoded* unit label
            # (e.g. "mg/kg bw/day") to validate that a text mention is
            # even comparable to the stored value, but unit decoding
            # only happens later (see _decode_in_place, which needs the
            # codebook). Computed here with unit_label=None unresolved
            # as "unsupported unit" by design; _decode_in_place
            # recomputes it once the unit label is known.
            entry["chemical_basis"] = extract_chemical_basis(
                entry["value"] or entry["lower_value"],
                entry["justification_and_comments"],
                stored_unit_label=None,
            ).to_dict()
            results.append(entry)
    return results


def derive_endpoint(raw_fields: list[dict]) -> dict:
    """Generic derived view shared by ENDPOINT_SUMMARY and ENDPOINT_STUDY_RECORD."""
    idx = _fields_index(raw_fields)
    key_information = _first_text(idx, "KeyInformation/KeyInformation")
    discussion = _first_text(idx, "Discussion/Discussion")
    species_code = _first_text(idx, "TestAnimals/Species/value")
    sex_code = _first_text(idx, "TestAnimals/Sex/value")
    duration = _first_text(idx, "AdministrationExposure/DurationOfTreatmentExposure")
    n_animals = _first_text(idx, "AdministrationExposure/NoOfAnimalsPerSexPerDose")
    endpoint_code = _first_text(idx, "AdministrativeData/Endpoint/value")
    data_source_ref = None
    for path, entries in idx.items():
        if path.endswith("DataSource/Reference/i6:key") or path.endswith("DataSource/Reference/key"):
            data_source_ref = entries[0]["text"]

    effect_levels = []
    current: dict | None = None
    last_uuid = None
    for f in raw_fields:
        if "Efflevel/entry" not in f["path"]:
            continue
        tail = f["path"].split("Efflevel/entry", 1)[1].lstrip("/")
        entry_uuid = f.get("entry_uuid") or (f.get("attrs") or {}).get("uuid")
        if entry_uuid and entry_uuid != last_uuid:
            if current:
                effect_levels.append(current)
            current = {
                "uuid": entry_uuid,
                "endpoint_type_code": None,
                "endpoint_type_label": None,
                "lower_value": None,
                "upper_value": None,
                "value": None,
                "unit_code": None,
                "basis_code": None,
                "remarks_code": None,
                "remarks_label": None,
                "remarks_text": None,
            }
            last_uuid = entry_uuid
        if current is None:
            continue
        if tail.endswith("Endpoint/value"):
            current["endpoint_type_code"] = f["text"]
        elif tail.endswith("Endpoint/other"):
            current["endpoint_type_label"] = f["text"]
        elif tail.endswith("EffectLevel/lowerValue"):
            current["lower_value"] = f["text"]
        elif tail.endswith("EffectLevel/upperValue"):
            current["upper_value"] = f["text"]
        elif tail.endswith("EffectLevel/value"):
            current["value"] = f["text"]
        elif tail.endswith("EffectLevel/unitCode"):
            current["unit_code"] = f["text"]
        elif tail.endswith("Basis/value"):
            current["basis_code"] = f["text"]
        elif tail.endswith("RemarksOnResults/value"):
            current["remarks_code"] = f["text"]
        elif tail.endswith("RemarksOnResults/other"):
            current["remarks_label"] = f["text"]
        elif tail.endswith("RemarksOnResults/remarks"):
            current["remarks_text"] = f["text"]
    if current:
        effect_levels.append(current)

    # ResultsAndDiscussion carries many other free-text result fields whose
    # tag names vary by subtype (e.g. BasicToxicokinetics's
    # DetailsOnDistribution/OtherInformation, RepeatedDoseToxicity's
    # narrative remarks). Rather than hand-model every subtype's result
    # schema, generically surface every remaining free-text leaf under
    # ResultsAndDiscussion that EffectLevels above didn't already claim, so
    # this content is not confined to raw_fields alone for the largest
    # study-record subtypes in this dataset.
    additional_results_text = []
    for f in raw_fields:
        path = f["path"]
        if "ResultsAndDiscussion" not in path or "EffectLevels" in path:
            continue
        if not f.get("text"):
            continue
        additional_results_text.append({"field": path.split("/")[-1], "path": path, "text": f["text"]})

    return {
        "key_information": key_information,
        "discussion": discussion,
        "species_code": species_code,
        "sex_code": sex_code,
        "duration_of_treatment_exposure": duration,
        "n_animals_per_sex_per_dose": n_animals,
        "endpoint_code": endpoint_code,
        "data_source_ref": data_source_ref,
        "effect_levels": effect_levels,
        "additional_results_text": additional_results_text,
    }


def build_document_record(
    archive_rel_path: str,
    entry_name: str,
    xml_bytes: bytes,
    codebook: Codebook | None,
) -> dict[str, Any]:
    parsed = parse_i6d(xml_bytes)
    meta = parsed["metadata"]
    doc_type = meta.get("documentType")
    sub_type = meta.get("documentSubType")
    domain = classify(doc_type, sub_type)
    parse_warnings: list[str] = []

    derived: dict[str, Any] = {}
    if doc_type == "DOSSIER":
        derived = derive_dossier(parsed["raw_fields"])
    elif doc_type == "SUBSTANCE":
        derived = derive_substance(parsed["raw_fields"])
    elif doc_type == "REFERENCE_SUBSTANCE":
        derived = derive_reference_substance(parsed["raw_fields"])
    elif doc_type == "LEGAL_ENTITY":
        derived = derive_legal_entity(parsed["raw_fields"])
    elif doc_type == "LITERATURE":
        derived = derive_literature(parsed["raw_fields"])
    elif doc_type == "FLEXIBLE_SUMMARY" and sub_type == "ToxRefValues":
        derived = {"reference_values": derive_reference_values(parsed["raw_fields"])}
        # derive_reference_values flattens each container type (ADI/ARfD/AOEL/
        # AAOEL/Other) into a single dict on the assumption that it occurs at
        # most once per document (confirmed on a large sample of this
        # dataset — see docs/OPENFOODTOX_DATASET_AUDIT.md). If that ever
        # doesn't hold for a given document, flag it instead of silently
        # merging two distinct reference values into one record.
        for container_tag in _REF_VALUE_CONTAINERS:
            if parsed["tag_counts"].get(container_tag, 0) > 1:
                parse_warnings.append(
                    f"{container_tag} appears {parsed['tag_counts'][container_tag]} times in this "
                    "document; reference-value extraction assumes at most one occurrence and may "
                    "have merged distinct entries — treat derived.reference_values for this "
                    "document as needing manual review."
                )
    elif doc_type in ("ENDPOINT_SUMMARY", "ENDPOINT_STUDY_RECORD"):
        derived = derive_endpoint(parsed["raw_fields"])

    if codebook is not None:
        xsl_name = _xsl_name_for(doc_type, sub_type)
        _decode_in_place(derived, codebook, xsl_name)

    if parsed["raw_fields_truncated"]:
        # Quarantine: a truncated document's derived view may be missing
        # fields that exist beyond the coverage cap, so it must not be
        # silently counted as usable evidence anywhere downstream (see
        # dossier.py / extract.py, which check this flag before counting
        # a document as usable human-health evidence or surfacing it in
        # a profile without a caveat).
        parse_warnings.append(
            f"raw field extraction stopped at {MAX_RAW_FIELDS_PER_DOCUMENT} leaves for this "
            "document; derived fields may be incomplete and must not be treated as usable "
            "evidence without manual review (see evidence_complete=False)."
        )
    if parsed["raw_fields_safety_ceiling_hit"]:
        parse_warnings.append(
            f"raw field extraction hit the hard safety ceiling ({_SAFETY_CEILING_LEAVES} leaves); "
            "this document may have far more content than was walked at all."
        )

    return {
        "document_key": meta.get("documentKey"),
        "parent_document_key": meta.get("parentDocumentKey"),
        "name": meta.get("name"),
        "document_type": doc_type,
        "document_sub_type": sub_type,
        "domain": domain,
        "creation_date": meta.get("creationDate"),
        "last_modification_date": meta.get("lastModificationDate"),
        "derived": derived,
        "raw_fields": parsed["raw_fields"],
        "raw_fields_truncated": parsed["raw_fields_truncated"],
        "raw_fields_safety_ceiling_hit": parsed["raw_fields_safety_ceiling_hit"],
        # False whenever raw-field extraction was truncated -- callers
        # must exclude such documents from "usable evidence" counts and
        # surface them as incomplete rather than silently include them.
        "evidence_complete": not parsed["raw_fields_truncated"],
        "parse_warnings": parse_warnings,
        "source": {
            "archive": archive_rel_path,
            "entry": entry_name,
            "document_key": meta.get("documentKey"),
        },
    }


def _xsl_name_for(doc_type: str | None, sub_type: str | None) -> str | None:
    if doc_type is None:
        return None
    if sub_type:
        return f"{doc_type}-{sub_type}.xsl"
    return f"{doc_type}.xsl"


def _decode_in_place(derived: dict, codebook: Codebook, xsl_name: str | None) -> None:
    for key in list(derived.keys()):
        if key.endswith("_code"):
            label_key = key[: -len("_code")] + "_label"
            if label_key in derived and derived[label_key]:
                continue  # already self-describing via sibling <other>
            code = derived.get(key)
            if code is None:
                continue
            if "unit" in key:
                decoded = codebook.decode_unit(code)
            else:
                decoded = codebook.decode_value(xsl_name, code)
            if decoded:
                derived[label_key] = decoded
                derived.setdefault(label_key + "_source", "shipped_xsl_stylesheet")
    if isinstance(derived.get("effect_levels"), list):
        for el in derived["effect_levels"]:
            if el.get("unit_code") and not el.get("unit_label"):
                decoded = codebook.decode_unit(el["unit_code"])
                if decoded:
                    el["unit_label"] = decoded
                    el["unit_label_source"] = "shipped_xsl_stylesheet"
            if el.get("basis_code") and not el.get("basis_label"):
                decoded = codebook.decode_value(xsl_name, el["basis_code"])
                if decoded:
                    el["basis_label"] = decoded
                    el["basis_label_source"] = "shipped_xsl_stylesheet"
    if isinstance(derived.get("reference_values"), list):
        for rv in derived["reference_values"]:
            if rv.get("unit_code") and not rv.get("unit_label"):
                decoded = codebook.decode_unit(rv["unit_code"])
                if decoded:
                    rv["unit_label"] = decoded
                    rv["unit_label_source"] = "shipped_xsl_stylesheet"
            if rv.get("population_code") and not rv.get("population_label"):
                decoded = codebook.decode_value(xsl_name, rv["population_code"])
                if decoded:
                    rv["population_label"] = decoded
                    rv["population_label_source"] = "shipped_xsl_stylesheet"
            # Recompute now that unit_label (if any) is decoded --
            # chemical-basis matching must validate the stored value's
            # unit, which was not yet known when derive_reference_values
            # first ran (see the comment there).
            rv["chemical_basis"] = extract_chemical_basis(
                rv.get("value") or rv.get("lower_value"),
                rv.get("justification_and_comments"),
                stored_unit_label=rv.get("unit_label"),
            ).to_dict()
