"""Tests for scripts/openfoodtox/build_app_pilot_content.py -- the
structured, versioned EN/BG content artifact for the four owner-approved
OpenFoodTox pilot identities (docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md).
"""

from scripts.openfoodtox.build_app_pilot_content import build_artifact
from scripts.openfoodtox.editorial_content import EDITORIAL_CONTENT


def test_covers_exactly_the_four_allowlisted_identities():
    artifact = build_artifact()
    assert set(artifact["profiles"].keys()) == {"E250", "E150d", "E330", "E951"}
    assert artifact["allowlisted_e_numbers"] == ["E250", "E150d", "E330", "E951"]


def test_never_touches_regulatory_approval_or_scoring_fields():
    """Only the fields app/seed/load_openfoodtox_pilot_content.py is
    allowed to write exist in the artifact at all -- acceptable_daily_intake,
    efsa_status, fda_status, risk_level and every dietary/scoring flag
    must never appear here, so there is no way for the import path to
    accidentally source them from this artifact."""
    artifact = build_artifact()
    forbidden_keys = {
        "acceptable_daily_intake", "efsa_status", "fda_status", "risk_level",
        "risk_assessment_available", "verification_status", "is_gluten", "is_vegan",
        "is_vegetarian", "is_halal", "is_kosher", "bad_for_diabetes", "bad_for_hypertension",
        "bad_for_kidney_disease", "bad_for_gout", "bad_for_pregnancy", "bad_for_children",
        "bad_for_high_cholesterol",
    }
    for profile in artifact["profiles"].values():
        assert forbidden_keys.isdisjoint(profile.keys())


def test_every_profile_has_bilingual_description_and_purpose():
    artifact = build_artifact()
    for e_number, profile in artifact["profiles"].items():
        assert profile["description"]["en"], e_number
        assert profile["description"]["bg"], e_number
        assert profile["purpose_in_food"]["en"], e_number
        assert profile["purpose_in_food"]["bg"], e_number


def test_e150d_carries_new_ingredient_display_and_withheld_intake_explanation():
    artifact = build_artifact()
    e150d = artifact["profiles"]["E150d"]
    assert e150d["new_ingredient_display"]["id"] == "e150d_sulphite_ammonia_caramel"
    assert e150d["new_ingredient_display"]["common_name"]["en"]
    assert e150d["new_ingredient_display"]["common_name"]["bg"]
    # Explains the withheld number in prose -- never a fabricated figure.
    assert "shared" in e150d["dietary_guidance"]["en"].lower()
    assert e150d["dietary_guidance"]["bg"]


def test_other_three_identities_have_no_new_ingredient_display():
    artifact = build_artifact()
    for e_number in ("E250", "E330", "E951"):
        assert "new_ingredient_display" not in artifact["profiles"][e_number]


def test_health_concerns_includes_every_effects_note_text():
    artifact = build_artifact()
    for e_number, profile in artifact["profiles"].items():
        entry = EDITORIAL_CONTENT[e_number]
        for note in entry.effects:
            assert note.text.en in profile["health_concerns"]["en"]
            assert note.text.bg in profile["health_concerns"]["bg"]


def test_operator_only_notes_never_leak_into_the_artifact():
    artifact = build_artifact()
    for e_number, profile in artifact["profiles"].items():
        entry = EDITORIAL_CONTENT[e_number]
        serialized = str(profile)
        for note in entry.operator_only_notes:
            assert note.en not in serialized
            assert note.bg not in serialized


def test_is_deterministic():
    assert build_artifact() == build_artifact()


def test_content_version_is_stable_and_present():
    artifact = build_artifact()
    assert artifact["content_version"].startswith("openfoodtox-app-pilot-v")
    assert artifact["owner_publication_permission"] is True
    assert artifact["scientific_review_status"] == "not_reviewed"
    assert artifact["translation_review_status"] == "not_reviewed"
