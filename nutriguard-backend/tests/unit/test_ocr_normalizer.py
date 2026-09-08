from app.models.enums import RiskLevel
from app.services.ocr_normalizer import (
    create_synthetic_ingredient,
    match_against_database,
    normalize_and_extract_tokens,
    reconstruct_synthetic_ingredient,
)


def test_tokenizes_comma_separated_ingredients():
    tokens = normalize_and_extract_tokens("Water, Sugar, Citric Acid")
    assert tokens == ["Water", "Sugar", "Citric Acid"]


def test_strips_brackets_and_percent_parens():
    tokens = normalize_and_extract_tokens("Palm Oil [sustainably sourced], Cocoa (10%)")
    assert tokens == ["Palm Oil", "Cocoa"]


def test_strips_ingredients_prefix_case_insensitive():
    tokens = normalize_and_extract_tokens("INGREDIENTS: Water, Salt")
    assert tokens == ["Water", "Salt"]


def test_filters_single_character_tokens():
    tokens = normalize_and_extract_tokens("Water, A, Salt")
    assert "A" not in tokens


def test_match_against_database_by_e_number():
    class FakeIngredient:
        id = "e951_aspartame"
        common_name = "Aspartame"
        scientific_name = "L-alpha-aspartyl..."
        e_number = "E951"

    result = match_against_database(["Aspartame (E951)"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1
    assert result.matched_ingredients[0].id == "e951_aspartame"


def test_match_against_database_by_partial_name():
    class FakeIngredient:
        id = "sugar"
        common_name = "Sugar"
        scientific_name = "Sucrose"
        e_number = None

    result = match_against_database(["Cane Sugar"], [FakeIngredient()])
    assert len(result.matched_ingredients) == 1


def test_unknown_tokens_go_to_unknown_list():
    result = match_against_database(["Unobtainium"], [])
    assert result.unknown_ingredients == ["Unobtainium"]
    assert result.matched_ingredients == []


def test_synthetic_ingredient_never_infers_risk_from_keywords():
    """Data-quality task: an OCR-only ingredient with no scientific-
    database match must never become HIGH_CONCERN/POTENTIAL_CONCERN
    just because its OCR name contains a scary/moderate keyword -- that
    was fabricated risk, not a real assessment. `risk_level` is always
    the neutral SAFE placeholder, and `risk_assessment_available` is
    what actually signals "not really assessed"."""
    high_risk_keyword_name = create_synthetic_ingredient("Sodium Nitrite")
    moderate_keyword_name = create_synthetic_ingredient("Corn Syrup")
    no_keyword_name = create_synthetic_ingredient("Water")

    for syn in (high_risk_keyword_name, moderate_keyword_name, no_keyword_name):
        assert syn.risk_level == RiskLevel.SAFE
        assert syn.risk_assessment_available is False

    assert high_risk_keyword_name.id.startswith("synth_")


def test_synthetic_ingredient_has_no_fabricated_scientific_or_regulatory_data():
    """None of the historical fictitious placeholder strings this task
    removes may ever be produced again, and every field that would
    require real curated/verified data is honestly empty, not a fake
    generic claim. OCR is provenance, not scientific evidence."""
    syn = create_synthetic_ingredient("Sodium Nitrite")

    forbidden_substrings = (
        "Normalized Food Component",
        "extracted via OCR",
        "Food component / formulation ingredient",
        "Standard ingredient",
        "Standard Food Additive",
        "Recognized Ingredient",
        "standard local food safety regulations",
        "Standard dietary intake",
        "individual sensitivity profile",
        "NutriGuard OCR & Scientific Pipeline",
    )
    serialized = " ".join(
        str(value)
        for value in (
            syn.scientific_name,
            syn.description,
            syn.purpose_in_food,
            syn.health_concerns,
            syn.evidence_level,
            syn.countries_restricted_or_banned,
            syn.efsa_status,
            syn.fda_status,
            syn.acceptable_daily_intake,
            syn.side_effects,
            syn.references,
        )
    ).lower()
    for forbidden in forbidden_substrings:
        assert forbidden.lower() not in serialized, forbidden

    assert syn.description == ""
    assert syn.purpose_in_food == ""
    assert syn.health_concerns == ""
    assert syn.evidence_level == ""
    assert syn.countries_restricted_or_banned == ""
    assert syn.efsa_status == ""
    assert syn.fda_status == ""
    assert syn.acceptable_daily_intake == ""
    assert syn.side_effects == ""
    assert syn.references == ""
    assert syn.who_iarc_classification is None
    assert syn.scientific_name == ""  # no E-number in this name to report


def test_synthetic_ingredient_scientific_name_reports_a_genuine_e_number():
    """A real E-number literally present in the OCR text is genuine
    provenance (not a scientific claim) and may still be reported."""
    syn = create_synthetic_ingredient("some e621 flavor enhancer")
    assert syn.scientific_name == "E621"


def test_synthetic_ingredient_never_infers_bad_for_flags_from_name_keywords():
    """PR #13 review fix (task requirement 2): `bad_for_*` used to be
    guessed from a handful of substrings in the raw OCR name (e.g.
    "sugar"/"syrup"/"dextrose" -> `bad_for_diabetes=True`,
    "sodium"/"salt"/"msg" -> `bad_for_hypertension=True`, "palm"/"fat"/
    "hydrogenated" -> `bad_for_high_cholesterol=True`) -- a bare name
    substring is not a clinical assessment, and a wrongly-guessed `True`
    here used to drive a real HIGH-severity personalized warning (see
    `app.services.warning_engine`) for a condition never actually
    evaluated. Every one of these must now be `False` -- "not flagged",
    never a keyword-derived medical claim -- for every OCR-only
    ingredient, regardless of which keyword its name happens to contain.
    """
    for name in ("Dextrose", "Corn Syrup", "Sodium Chloride", "Table Salt", "MSG", "Palm Oil", "Hydrogenated Fat"):
        syn = create_synthetic_ingredient(name)
        assert syn.bad_for_diabetes is False, name
        assert syn.bad_for_hypertension is False, name
        assert syn.bad_for_high_cholesterol is False, name
        assert syn.bad_for_kidney_disease is False, name
        assert syn.bad_for_gout is False, name
        assert syn.bad_for_pregnancy is False, name
        assert syn.bad_for_children is False, name


def test_synthetic_ingredient_never_claims_a_dietary_identity():
    """PR #13 review fix (task requirement 2): `is_vegan`/`is_vegetarian`/
    `is_halal`/`is_kosher` used to default to `True` (a fabricated
    positive certification) and `is_gluten`/`is_lactose` to `False` (a
    fabricated "confirmed free of" claim) for every OCR-only ingredient.
    All six must now be `None` -- genuinely unknown -- never a value
    that reads as a real assessment."""
    syn = create_synthetic_ingredient("Whey Protein Isolate")
    assert syn.is_gluten is None
    assert syn.is_lactose is None
    assert syn.is_vegan is None
    assert syn.is_vegetarian is None
    assert syn.is_halal is None
    assert syn.is_kosher is None


def test_synthetic_ingredient_never_persists_none_as_proof_of_no_allergens():
    """PR #13 review fix (task requirement 2): the literal string
    "None" used to be written whenever no allergen keyword matched --
    read back, that looks exactly like a verified clean bill of health
    rather than "not stated". A single ingredient-name token can never
    prove the ABSENCE of an allergen (only a positive match is real
    evidence), so the negative case must be an honest, empty "unknown"
    instead."""
    syn = create_synthetic_ingredient("Citric Acid")
    assert syn.allergens == ""
    assert syn.allergens.strip().lower() != "none"

    # A positive match is real evidence straight from the OCR text and
    # is unaffected by this fix.
    milk_syn = create_synthetic_ingredient("Whole Milk Powder")
    assert milk_syn.allergens == "Potential Allergen"


def test_synthetic_ingredient_extracts_e_number():
    syn = create_synthetic_ingredient("some e621 flavor enhancer")
    assert syn.e_number == "E621"


def test_synthetic_ingredient_non_latin_name_gets_a_stable_non_colliding_id():
    """A name with no ASCII-alphanumeric characters at all (e.g. a
    Cyrillic-only Bulgarian ingredient word -- see
    app.services.label_language) must not collapse to the same empty
    "synth_" id as every other such name."""
    water = create_synthetic_ingredient("Вода")
    sugar = create_synthetic_ingredient("Захар")
    assert water.id != "synth_"
    assert sugar.id != "synth_"
    assert water.id != sugar.id
    # Deterministic: the same name always yields the same id.
    assert create_synthetic_ingredient("Вода").id == water.id


def test_reconstruct_synthetic_ingredient_recovers_cyrillic_human_name():
    original = create_synthetic_ingredient("Вода")
    restored = reconstruct_synthetic_ingredient(original.id, "Вода, Захар")
    assert restored.id == original.id
    assert restored.common_name == "Вода"
    assert "synth" not in restored.common_name.lower()


def test_reconstruct_synthetic_ingredient_never_displays_hash_id():
    restored = reconstruct_synthetic_ingredient("synth_5ecbec8146", "")
    assert restored.id == "synth_5ecbec8146"
    assert restored.common_name == "Ingredient detected on label"


# --- Bounded, collision-resistant synthetic ids (PR #13 review: --------------
# --- requirement 1, "Bound every generated synthetic ingredient ID") --------

_MAX_ID_LEN = 64  # app/models/ingredient.py: `Ingredient.id` is `String(64)`


def test_very_long_ascii_name_produces_an_id_within_the_database_limit():
    long_name = "Modified Corn Starch Hydrolysate Emulsifier Stabilizer " * 5  # ~300 chars
    syn = create_synthetic_ingredient(long_name)
    assert len(syn.id) <= _MAX_ID_LEN
    assert syn.id.startswith("synth_")
    # Deterministic: re-generating from the identical name yields the
    # identical id.
    assert create_synthetic_ingredient(long_name).id == syn.id


def test_very_long_bulgarian_unicode_name_produces_an_id_within_the_database_limit():
    long_cyrillic_name = "Модифицирано царевично нишесте хидролизат емулгатор стабилизатор " * 4
    syn = create_synthetic_ingredient(long_cyrillic_name)
    assert len(syn.id) <= _MAX_ID_LEN
    assert syn.id.startswith("synth_")
    assert create_synthetic_ingredient(long_cyrillic_name).id == syn.id


def test_punctuation_heavy_name_produces_a_valid_bounded_id():
    punctuation_heavy_name = "!!!Sodium-Benzoate/Potassium.Sorbate (E211/E202) -- 99.9%!!! @@@###"
    syn = create_synthetic_ingredient(punctuation_heavy_name)
    assert len(syn.id) <= _MAX_ID_LEN
    assert syn.id.startswith("synth_")
    # Only the id's fixed prefix/separator/hash characters are
    # guaranteed -- no raw punctuation from the name leaks into it.
    import re

    assert re.fullmatch(r"synth_[a-z0-9_]*", syn.id)
    assert create_synthetic_ingredient(punctuation_heavy_name).id == syn.id


def test_colliding_prefix_names_still_produce_distinct_ids():
    """Two long names that share the same first characters (so their
    TRUNCATED slug would be identical) must still resolve to different
    ids -- the content hash covers the FULL name, not the truncated
    slug, which is what actually guarantees uniqueness (see
    `ocr_normalizer._synthetic_id`)."""
    shared_prefix = "Highly Specific Compound Additive Formulation Batch " * 3
    name_a = shared_prefix + "Variant Alpha"
    name_b = shared_prefix + "Variant Beta"
    syn_a = create_synthetic_ingredient(name_a)
    syn_b = create_synthetic_ingredient(name_b)

    assert len(syn_a.id) <= _MAX_ID_LEN
    assert len(syn_b.id) <= _MAX_ID_LEN
    assert syn_a.id != syn_b.id
    # The truncated slug portion IS expected to collide (both names
    # share the same first `_MAX_SLUG_LEN` characters) -- only the hash
    # suffix disambiguates them.
    assert syn_a.id.rsplit("_", 1)[0] == syn_b.id.rsplit("_", 1)[0]
    assert syn_a.id.rsplit("_", 1)[1] != syn_b.id.rsplit("_", 1)[1]


def test_short_and_long_names_never_collide_with_each_other():
    short = create_synthetic_ingredient("Salt")
    long = create_synthetic_ingredient("Salt" + " Extended Description Text" * 10)
    assert short.id != long.id


def test_reconstruct_synthetic_ingredient_recovers_a_long_ascii_name_via_id_fallback():
    """When the original raw OCR text is genuinely unavailable (only the
    persisted id remains), the CURRENT id shape's readable slug prefix
    is still recovered -- never the hash suffix -- for a long name that
    was truncated at creation time."""
    long_name = "Modified Corn Starch Hydrolysate Emulsifier Stabilizer " * 5
    original = create_synthetic_ingredient(long_name)
    restored = reconstruct_synthetic_ingredient(original.id, "")
    assert restored.id == original.id
    # The hash suffix (the id's last 12 characters) must never leak into
    # the displayed name.
    content_hash = original.id[-12:]
    assert content_hash not in restored.common_name.lower()
    assert "modified corn starch" in restored.common_name.lower()
