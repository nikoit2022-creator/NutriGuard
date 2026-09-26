"""
Issue #23 stage 3: pure rules of the ingredient-summary layer (no database).

Covers the structural contract of a content subject, exact-identity subject
keys, the served-payload gates (English only when SOURCE_VERIFIED, Bulgarian
only when REVIEWED and hash-current) and the committed pilot content file.
"""
import copy
import json
from types import SimpleNamespace

import pytest

from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization
from app.seed import load_summaries as loader
from app.services import ingredient_summaries as svc


def _subject(**overrides) -> dict:
    subject = {
        "subjectKey": "E999",
        "scope": "E_NUMBER_GENERIC",
        "sections": [
            {"kind": "ORIGIN", "text": "Made from x.", "citationIds": ["a"]},
            {"kind": "FUNCTION", "text": "Used as y.", "citationIds": ["a", "b"]},
            {"kind": "JURISDICTION", "jurisdiction": "EU", "text": "EU rule.", "citationIds": ["b"]},
        ],
        "citations": [
            {"id": "a", "label": "A", "url": "https://example.org/a", "documentDate": "2020", "accessType": "FULL_TEXT"},
            {"id": "b", "label": "B", "url": "https://example.org/b", "documentDate": "2021", "accessType": "ABSTRACT"},
        ],
        "claims": [{"section": "ORIGIN", "claim": "x", "citationId": "a", "locator": "p1"}],
    }
    subject.update(overrides)
    return subject


def _row(**overrides) -> IngredientSummary:
    sections = [
        {"kind": "ORIGIN", "text": "Made from x.", "citationIds": ["a"]},
        {"kind": "JURISDICTION", "text": "EU rule.", "citationIds": ["a"], "jurisdiction": "EU"},
    ]
    citations = [
        {"id": "a", "label": "A", "url": "https://example.org/a", "documentDate": "2020", "accessType": "FULL_TEXT"},
        {"id": "unused", "label": "U", "url": "https://example.org/u", "documentDate": "2020", "accessType": "FULL_TEXT"},
    ]
    row = IngredientSummary(
        subject_key="E999",
        scope="E_NUMBER_GENERIC",
        evidence_state="SOURCE_VERIFIED",
        human_reviewed=False,
        sections_json=json.dumps(sections),
        citations_json=json.dumps(citations),
        claims_json="[]",
        content_hash=svc.content_hash(sections, citations),
        source_verified_at=None,
    )
    row.localizations = []
    row.__dict__.update(overrides)
    return row


def _bg(row: IngredientSummary, *, status="REVIEWED", digest=None, sections=None) -> IngredientSummaryLocalization:
    return IngredientSummaryLocalization(
        summary_id=1,
        language="bg",
        sections_json=json.dumps(
            sections
            or [
                {"kind": "ORIGIN", "text": "Направено от x."},
                {"kind": "JURISDICTION", "text": "Правило на ЕС.", "jurisdiction": "EU"},
            ]
        ),
        translation_status=status,
        translation_source="MACHINE_TRANSLATED",
        source_content_hash=digest or row.content_hash,
    )


# ---------------------------------------------------------------- identity


def test_subject_keys_are_exact_and_normalized():
    assert svc.subject_key_for_e_number(" e150 d ") == "E150D"
    assert svc.subject_key_for_name("Palm Oil.") == "name:palm oil"


def test_an_ingredient_matches_its_e_number_first_then_its_exact_name():
    ing = SimpleNamespace(e_number="E150d", common_name="Colour (E150d", identity_uncertain=False)
    assert svc.candidate_subject_keys(ing) == ["E150D", "name:colour (e150d"]


def test_no_fuzzy_or_family_prefix_matching():
    # E150 (plain caramel, unspecified) must never pick up the E150d subject.
    assert svc.candidate_subject_keys(SimpleNamespace(e_number="E150", common_name="", identity_uncertain=False)) == ["E150"]
    # Different name spellings are different subjects.
    assert "name:sugar" not in svc.candidate_subject_keys(
        SimpleNamespace(e_number=None, common_name="Cane sugar", identity_uncertain=False)
    )
    assert "name:sugar" not in svc.candidate_subject_keys(
        SimpleNamespace(e_number=None, common_name="Sugars", identity_uncertain=False)
    )


def test_an_unverifiable_name_still_matches_its_official_e_number_but_never_its_name():
    ing = SimpleNamespace(
        e_number="E150d", common_name="Colour (E150d", identity_uncertain=True, uncertainty_reason="TRANSLATION_UNRELIABLE"
    )
    assert svc.candidate_subject_keys(ing) == ["E150D"]
    # No code: nothing to anchor on, and the name is exactly what is unverified.
    name_only = SimpleNamespace(
        e_number=None, common_name="Sugar", identity_uncertain=True, uncertainty_reason="TRANSLATION_UNRELIABLE"
    )
    assert svc.candidate_subject_keys(name_only) == []


@pytest.mark.parametrize("reason", ["COLON_SEPARATED_CLAUSE_MERGE", "DUPLICATE_TOKEN_FRAGMENT", None, "SOMETHING_NEW"])
def test_a_token_that_may_be_merged_clauses_gets_no_subject_at_all(reason):
    ing = SimpleNamespace(e_number="E322", common_name="Sugar", identity_uncertain=True, uncertainty_reason=reason)
    assert svc.candidate_subject_keys(ing) == []


# --------------------------------------------------------------- validation


def test_a_well_formed_subject_has_no_problems():
    assert svc.validate_subject(_subject()) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.update(scope="OTHER"), "unknown scope"),
        (lambda s: s.update(subjectKey="name:x"), "scope does not match the key form"),
        (lambda s: s.update(sections=[]), "no sections"),
        (lambda s: s["sections"][0].update(text="  "), "empty ORIGIN section"),
        (lambda s: s["sections"][0].update(citationIds=[]), "ORIGIN section has no citation"),
        (lambda s: s["sections"][0].update(citationIds=["zzz"]), "unknown id"),
        (lambda s: s["sections"][0].update(kind="OTHER"), "unknown section kind"),
        (lambda s: s["sections"].reverse(), "out of order"),
        (lambda s: s["sections"][0].update(jurisdiction="EU"), "jurisdiction must be set on JURISDICTION sections only"),
        (lambda s: s["sections"][2].pop("jurisdiction"), "jurisdiction must be set on JURISDICTION sections only"),
        (lambda s: s["citations"][0].update(url="http://example.org/a"), "not https"),
        (lambda s: s["citations"][0].update(accessType="HEARSAY"), "unknown accessType"),
        (lambda s: s["citations"][0].pop("documentDate"), "lacks documentDate"),
        (lambda s: s["citations"].append(dict(s["citations"][0])), "duplicate citation ids"),
        (
            lambda s: s["citations"].append(
                {"id": "orphan", "label": "O", "url": "https://example.org/o", "documentDate": "2020", "accessType": "FULL_TEXT"}
            ),
            "is not used by any section",
        ),
    ],
)
def test_a_malformed_subject_is_rejected(mutate, expected):
    subject = copy.deepcopy(_subject())
    mutate(subject)
    problems = svc.validate_subject(subject)
    assert any(expected in p for p in problems), problems


def test_a_translation_that_does_not_line_up_with_the_english_sections_is_rejected():
    subject = _subject(
        localizations={"bg": {"sections": [{"kind": "ORIGIN", "text": "x"}, {"kind": "FUNCTION", "text": "y"}]}}
    )
    assert any("do not line up" in p for p in svc.validate_subject(subject))
    subject = _subject(
        localizations={
            "bg": {
                "sections": [
                    {"kind": "ORIGIN", "text": "x"},
                    {"kind": "FUNCTION", "text": ""},
                    {"kind": "JURISDICTION", "jurisdiction": "EU", "text": "z"},
                ]
            }
        }
    )
    assert any("empty section" in p for p in svc.validate_subject(subject))


# --------------------------------------------------------------- serving


def test_english_is_served_only_when_source_verified():
    assert svc.build_payload(_row(evidence_state="DRAFT")) is None
    assert svc.build_payload(None) is None
    payload = svc.build_payload(_row())
    assert payload["evidenceState"] == "SOURCE_VERIFIED"
    assert payload["humanReviewed"] is False
    assert [s["kind"] for s in payload["sections"]] == ["ORIGIN", "JURISDICTION"]
    assert "jurisdiction" not in payload["sections"][0]
    assert payload["sections"][1]["jurisdiction"] == "EU"


def test_only_cited_citations_are_served_with_what_they_support():
    payload = svc.build_payload(_row())
    assert [c["id"] for c in payload["citations"]] == ["a"]
    assert payload["citations"][0]["supports"] == ["ORIGIN", "JURISDICTION"]


def test_the_internal_claims_ledger_is_never_served():
    payload = svc.build_payload(_row(claims_json=json.dumps([{"claim": "secret ledger"}])))
    assert "claims" not in payload and "secret ledger" not in json.dumps(payload)


def test_a_reviewed_current_translation_is_served_and_carries_its_provenance():
    row = _row()
    row.localizations = [_bg(row)]
    payload = svc.build_payload(row)
    bg = payload["localizations"]["bg"]
    assert bg["translationStatus"] == "REVIEWED"
    assert bg["translationSource"] == "MACHINE_TRANSLATED"  # review does not change origin
    assert [s["kind"] for s in bg["sections"]] == ["ORIGIN", "JURISDICTION"]
    assert "citationIds" not in bg["sections"][0]  # citations stay language-independent


def test_a_draft_translation_is_never_served():
    row = _row()
    row.localizations = [_bg(row, status="DRAFT")]
    assert svc.build_payload(row)["localizations"] == {}


def test_a_stale_reviewed_translation_is_never_served():
    row = _row()
    row.localizations = [_bg(row, digest="0" * 64)]
    assert svc.build_payload(row)["localizations"] == {}


def test_a_reviewed_translation_that_lost_a_section_is_never_served():
    row = _row()
    row.localizations = [_bg(row, sections=[{"kind": "ORIGIN", "text": "Направено от x."}])]
    assert svc.build_payload(row)["localizations"] == {}


def test_the_content_hash_moves_with_english_text_and_citations_only():
    base = svc.content_hash([{"kind": "ORIGIN", "text": "a", "citationIds": ["x"]}], [{"id": "x"}])
    assert base == svc.content_hash([{"citationIds": ["x"], "text": "a", "kind": "ORIGIN"}], [{"id": "x"}])
    assert base != svc.content_hash([{"kind": "ORIGIN", "text": "b", "citationIds": ["x"]}], [{"id": "x"}])
    assert base != svc.content_hash([{"kind": "ORIGIN", "text": "a", "citationIds": ["x"]}], [{"id": "y"}])


# ------------------------------------------------------ the committed pilot


def _pilot() -> list[dict]:
    return loader.read_seed_file()


def test_the_committed_pilot_file_is_structurally_valid():
    keys = [s["subjectKey"] for s in _pilot()]
    assert keys == ["E150D", "E322", "name:sugar", "name:salt", "name:palm oil"]


def test_every_pilot_section_has_a_claim_in_the_ledger_and_every_claim_cites_a_listed_source():
    for subject in _pilot():
        cited = {c["id"] for c in subject["citations"]}
        claim_sections = {c["section"] for c in subject["claims"]}
        for sec in subject["sections"]:
            assert sec["kind"] in claim_sections, (subject["subjectKey"], sec["kind"])
        for claim in subject["claims"]:
            assert claim["citationId"] in cited, (subject["subjectKey"], claim)
            assert claim["locator"].strip(), (subject["subjectKey"], claim)


def test_no_pilot_translation_is_marked_reviewed_and_every_one_lines_up():
    for subject in _pilot():
        for loc in (subject.get("localizations") or {}).values():
            # The file carries text only: status/origin are decided by the loader (DRAFT), never by content.
            assert set(loc.keys()) == {"sections"}
            assert len(loc["sections"]) == len(subject["sections"])


def test_pilot_text_makes_no_unsupported_safety_or_risk_claims():
    banned = ("safe to consume", "is safe", "is harmless", "dangerous", "toxic to humans", "avoid ", "carcinogen to humans", "risk score")
    for subject in _pilot():
        text = " ".join(s["text"].lower() for s in subject["sections"])
        for phrase in banned:
            assert phrase not in text, (subject["subjectKey"], phrase)


def test_e150d_keeps_the_colour_mixture_the_4mei_by_product_and_animal_versus_human_evidence_apart():
    e150d = next(s for s in _pilot() if s["subjectKey"] == "E150D")
    origin = next(s for s in e150d["sections"] if s["kind"] == "ORIGIN")["text"]
    effects = next(s for s in e150d["sections"] if s["kind"] == "EFFECTS")["text"]
    assert "complex mixture" in origin and "separate substance" in origin and "not the colour itself" in origin
    assert "animal feeding studies of the pure substance, not from studies of E 150d itself" in effects
    assert "cites no human study" in effects and "depends on dose" in effects
    # No invented intake figure: the only ADI numbers are EFSA's own, attributed.
    assert "JECFA" not in effects
    # Regulatory statements name their jurisdiction and are separate sections.
    assert [s["jurisdiction"] for s in e150d["sections"] if s["kind"] == "JURISDICTION"] == ["EU", "US", "US-CA"]


def test_the_committed_review_pack_matches_the_content_file():
    """`docs/INGREDIENT_SUMMARY_BG_REVIEW.md` is generated by
    `python -m app.seed.summary_review export`. A reviewer quotes the hash
    printed there, so it must never drift from the committed content."""
    from pathlib import Path

    from app.seed import summary_review

    committed = (Path(__file__).resolve().parents[2] / "docs" / "INGREDIENT_SUMMARY_BG_REVIEW.md").read_text(
        encoding="utf-8"
    )
    assert committed.rstrip("\n") == summary_review.render_review_pack().rstrip("\n")
