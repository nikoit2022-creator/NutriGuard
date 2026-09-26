"""Issue #23 stage 2: `match_against_database` matches by identity evidence
only. A token that is merely a fragment of a name never matches; an
uncurated (OCR-derived) row matches only exactly."""
from types import SimpleNamespace

import pytest

from app.models.enums import IngredientSource
from app.services.ocr_normalizer import match_against_database


def _row(id_, name, *, e_number=None, scientific="", source=IngredientSource.OCR_HEURISTIC):
    return SimpleNamespace(id=id_, common_name=name, scientific_name=scientific, e_number=e_number, source=source)


def _ids(result):
    return [i.id for i in result.matched_ingredients]


@pytest.mark.parametrize(
    "token, row_name",
    [
        ("Water", "Carbonated Water"),
        ("Sugar", "Sugar-free sweetener"),
        ("Salt", "Salted butter"),
        ("Butter", "Salted butter"),
        ("Oil", "Palm oil"),
        ("Milk", "Skimmed milk powder"),
        ("Вода", "Газирана вода"),
    ],
)
@pytest.mark.parametrize("source", [IngredientSource.OCR_HEURISTIC, IngredientSource.CURATED_SEED])
def test_a_fragment_of_a_name_never_matches(token, row_name, source):
    result = match_against_database([token], [_row("r", row_name, source=source)])
    assert result.matched_ingredients == [] and result.unknown_ingredients == [token]


def test_the_curated_generic_token_does_not_collapse_into_a_specific_row():
    soy = _row("e322_soy_lecithin", "Soy Lecithin", e_number="E322", source=IngredientSource.CURATED_SEED)
    assert _ids(match_against_database(["Lecithin"], [soy])) == []
    assert _ids(match_against_database(["Lecithin (E322)"], [soy])) == ["e322_soy_lecithin"]  # official identifier


@pytest.mark.parametrize("token", ["Palm oil", "palm OIL.", "  PALM   oil "])
def test_an_uncurated_row_matches_its_exact_normalized_name(token):
    assert _ids(match_against_database([token], [_row("palm", "Palm oil")])) == ["palm"]


def test_an_uncurated_row_never_absorbs_a_more_specific_token():
    result = match_against_database(["Refined palm oil"], [_row("palm", "Palm oil")])
    assert result.matched_ingredients == [] and result.unknown_ingredients == ["Refined palm oil"]


def test_a_curated_row_may_absorb_a_token_containing_its_full_name_as_whole_words():
    sugar = _row("sugar", "Sugar", source=IngredientSource.CURATED_SEED)
    assert _ids(match_against_database(["Cane Sugar"], [sugar])) == ["sugar"]
    assert _ids(match_against_database(["Sugarcane"], [sugar])) == []  # not a whole word
    oat = _row("oat", "Oat", source=IngredientSource.CURATED_SEED)
    assert _ids(match_against_database(["Goat milk"], [oat])) == []


def test_an_exact_e_number_matches_any_row_including_an_uncurated_one():
    assert _ids(match_against_database(["Colour (E150d)"], [_row("c", "Caramel", e_number="E150D")])) == ["c"]
    assert _ids(match_against_database(["Colour (E150a)"], [_row("c", "Caramel", e_number="E150D")])) == []


def test_exact_scientific_name_matches_but_a_fragment_of_it_does_not():
    citric = _row("citric", "Citric Acid", scientific="2-hydroxypropane-1,2,3-tricarboxylic acid",
                  source=IngredientSource.CURATED_SEED)
    assert _ids(match_against_database(["2-Hydroxypropane-1,2,3-tricarboxylic acid"], [citric])) == ["citric"]
    assert _ids(match_against_database(["acid"], [citric])) == []


def test_an_exact_match_wins_over_a_containment_match():
    exact = _row("exact", "Cane Sugar")
    contained = _row("sugar", "Sugar", source=IngredientSource.CURATED_SEED)
    assert _ids(match_against_database(["Cane Sugar"], [contained, exact])) == ["exact"]


def test_duplicate_tokens_match_once_and_unknowns_stay_unknown():
    result = match_against_database(["Sugar", "sugar", "Unobtainium"], [_row("s", "Sugar")])
    assert _ids(result) == ["s"] and result.unknown_ingredients == ["Unobtainium"]
