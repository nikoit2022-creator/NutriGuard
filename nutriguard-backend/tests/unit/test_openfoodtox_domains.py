"""Tests for scripts/openfoodtox/domains.py: the documentSubType -> evidence
domain classification table, and its explicit unclassified fallback."""

from __future__ import annotations

from scripts.openfoodtox.domains import classify


def test_human_health_subtypes():
    assert classify("ENDPOINT_STUDY_RECORD", "RepeatedDoseToxicityOral") == "human_health"
    assert classify("ENDPOINT_SUMMARY", "Carcinogenicity_EU_PPP") == "human_health"
    assert classify("ENDPOINT_STUDY_RECORD", "EpidemiologicalData") == "human_health"


def test_environmental_subtypes_kept_separate_from_human_health():
    assert classify("ENDPOINT_STUDY_RECORD", "ShortTermToxicityToFish") == "environmental"
    assert classify("ENDPOINT_STUDY_RECORD", "ToxicityToBees") == "environmental"


def test_livestock_kept_separate_from_both_human_and_wildlife():
    assert classify("ENDPOINT_STUDY_RECORD", "ToxicEffectsLivestock") == "livestock_animal_health"


def test_physicochemical_subtypes():
    assert classify("ENDPOINT_STUDY_RECORD", "BoilingPoint") == "physicochemical"


def test_identity_document_types():
    assert classify("SUBSTANCE", None) == "identity"
    assert classify("REFERENCE_SUBSTANCE", None) == "identity"
    assert classify("LEGAL_ENTITY", None) == "identity"


def test_dossier_and_literature_are_assessment_summary():
    assert classify("DOSSIER", "EFSA_CHEMICALS_DATABASE") == "assessment_summary"
    assert classify("LITERATURE", None) == "assessment_summary"


def test_unknown_subtype_is_unclassified_not_guessed():
    assert classify("ENDPOINT_STUDY_RECORD", "SomeBrandNewSubtypeNeverSeenBefore") == "unclassified"
    assert classify(None, None) == "unclassified"
