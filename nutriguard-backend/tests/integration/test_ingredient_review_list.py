from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from app.models.ingredient_candidate import IngredientCandidate
from app.models.ingredient_alias import IngredientAlias
from app.models.enums import IngredientSource
from app.seed.ingredient_review_list import write_snapshot
from app.services.ingredient_catalog import materialize_ingredients
from app.services.ingredient_localization import canonical_text_hash
from app.services.ingredient_review_list import assemble_review_list, build_review_list
from app.services.ocr_normalizer import create_synthetic_ingredient


def candidate(id=1, ingredient_id="a", **changes):
    values = dict(id=id, ingredient_id=ingredient_id, status="PENDING", flags="",
                  encounter_count=1, first_seen_at=datetime(2026, 1, 1),
                  last_seen_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    return NS(**(values | changes))


def ingredient(id="a", name="Water", **changes):
    return NS(**(dict(id=id, common_name=name, e_number=None, localization_rows=[]) | changes))


def alias(text="Water", language="en", id="a"):
    return NS(ingredient_id=id, language=language, alias_text=text, alias_normalized=text.casefold())


def test_alias_observations_collapse_to_one_bilingual_identity():
    report = assemble_review_list([candidate(), candidate(2, encounter_count=4)], [ingredient()],
                                  [alias(), alias("Вода", "bg")])
    assert report["entryCount"] == 1
    row = report["entries"][0]
    assert (row["nameEn"], row["nameBg"]) == ("Water", "Вода")
    assert row["candidateIds"] == [1, 2]
    assert row["tokenObservationCount"] == 5
    assert row["firstSeenAt"].endswith("+00:00")


def test_foreign_and_unknown_names_not_printed_but_identity_remains():
    report = assemble_review_list([candidate()], [ingredient(name="Ulei de rapiță")],
                                  [alias("Ulei de rapiță", "ro")])
    row = report["entries"][0]
    assert row["nameEn"] is row["nameBg"] is None
    assert row["ingredientId"] == "a"
    assert "NAME_LANGUAGE_REVIEW_REQUIRED" in row["flags"]
    assert "rapiță" not in str(report)


@pytest.mark.parametrize("name", ["Ingredients: water", "synth_water_0123", "1234", "May contain milk"])
def test_artifacts_not_used_as_names(name):
    row = assemble_review_list([candidate()], [ingredient(name=name)], [alias(name)])["entries"][0]
    assert row["nameEn"] is None


def test_identical_names_on_distinct_identities_flagged_not_merged():
    rows = assemble_review_list([candidate(), candidate(2, "b")], [ingredient(), ingredient("b")],
                                [alias(), alias(id="b")])["entries"]
    assert len(rows) == 2
    assert all("NAME_COLLISION_REVIEW_REQUIRED" in r["flags"] for r in rows)


def test_junk_excluded_orphan_retained_without_original_text():
    report = assemble_review_list([candidate(status="JUNK"), candidate(2, None)], [], [])
    assert report["junkObservationRowsExcluded"] == 1
    assert report["entryCount"] == 1
    assert report["entries"][0]["reviewKey"] == "observation:2"


@pytest.mark.parametrize("status,stale,expected", [("REVIEWED", False, "Вода"), ("DRAFT", False, None), ("REVIEWED", True, None)])
def test_reviewed_current_bg_only(status, stale, expected):
    item = ingredient()
    item.localization_rows = [NS(language="bg", common_name="Вода", translation_status=status,
                                source_content_hash="stale" if stale else canonical_text_hash(item))]
    assert assemble_review_list([candidate()], [item], [alias()])["entries"][0]["nameBg"] == expected


def test_atomic_utf8_snapshot_and_failure_keeps_previous(tmp_path, monkeypatch):
    path = tmp_path / "review.json"
    write_snapshot(path, '{"nameBg":"Вода"}')
    assert "Вода" in path.read_text(encoding="utf-8")
    def fail(*args):
        raise OSError("simulated")
    monkeypatch.setattr("app.seed.ingredient_review_list.os.replace", fail)
    with pytest.raises(OSError):
        write_snapshot(path, "replacement")
    assert "Вода" in path.read_text(encoding="utf-8")
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
async def test_real_collector_then_read_only_report(db_session, monkeypatch):
    monkeypatch.setattr("app.services.ingredient_catalog.detect_language", lambda text: "en")
    await materialize_ingredients(db_session, [create_synthetic_ingredient("Wheat flour")])
    await db_session.commit()
    # Existing untagged aliases must not be guessed as English, even for a
    # plausible phrase. A reviewed language assignment makes it available.
    assert (await build_review_list(db_session))["entries"][0]["nameEn"] is None
    observed_alias = (await db_session.execute(select(IngredientAlias))).scalars().first()
    observed_alias.language = "en"
    await db_session.commit()
    before = (await db_session.execute(select(IngredientCandidate))).scalars().all()
    snapshot = [(r.id, r.encounter_count, r.status, r.flags) for r in before]
    report = await build_review_list(db_session)
    assert report["entryCount"] == 1
    assert report["entries"][0]["nameEn"] == "Wheat flour"
    assert report == await build_review_list(db_session)
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    after = (await db_session.execute(select(IngredientCandidate))).scalars().all()
    assert snapshot == [(r.id, r.encounter_count, r.status, r.flags) for r in after]


def test_mixed_language_name_not_accepted_by_stopword_heuristic():
    row = assemble_review_list([candidate()], [ingredient(name="Ingredients water ulei de rapiță")], [])["entries"][0]
    assert row["nameEn"] is row["nameBg"] is None


def test_curated_canonical_name_available_without_alias():
    row = assemble_review_list([candidate()], [ingredient(source=IngredientSource.CURATED_SEED)], [])["entries"][0]
    assert row["nameEn"] == "Water"
