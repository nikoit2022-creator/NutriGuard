"""
Migration d7e8f9a0b1c2 (tri-state product dietary flags, nullable Health
Score, honest allergens): chain/shape checks PLUS an executable check of
its legacy-data policy. The migration exposes the policy as plain
functions (`data_policy_statements` for the SQL half, `apply_flag_policy`
for the flag half, which needs per-occurrence text evaluation and so runs
in Python) and this test executes exactly those against representative
legacy rows in an in-memory SQLite database. (The DDL half -- ALTER COLUMN,
upgrade -> downgrade -> upgrade -- needs a real PostgreSQL: see
`tests/postgres/test_tristate_product_flags_migration_postgres.py`.)

PR #22 review, finding 1: a legacy `false` used to survive whenever the raw
text merely CONTAINED a keyword ("gluten" inside "gluten-free"). The policy
now keeps a `false` only when exact ingredient-entry identity or a trusted
catalog row still supports it; otherwise it is unknown.
"""
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "d7e8f9a0b1c2"
MIGRATION_FILE = BACKEND_ROOT / "alembic" / "versions" / "d7e8f9a0b1c2_tristate_product_flags_nullable_score.py"

FLAGS = ("is_gluten_free", "is_lactose_free", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_d7e8f9a0b1c2", MIGRATION_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIG = _load_migration()


def test_migration_is_the_single_head_and_chains_from_the_previous_head():
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_revision(REVISION).down_revision == "c6d7e8f9a0b1"
    assert scripts.get_heads() == [REVISION]


def test_migration_covers_exactly_the_intended_columns_and_backfills_before_not_null():
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    for column in (*FLAGS, "health_score"):
        assert f'"{column}"' in source
    assert "nullable=True" in source and "nullable=False" in source
    downgrade_body = source.split("def downgrade()")[1]
    # every backfill statement runs before the first NOT NULL is re-added
    assert downgrade_body.index("downgrade_backfill_statements") < downgrade_body.index("nullable=False")
    upgrade_body = source.split("def upgrade()")[1].split("def downgrade()")[0]
    # columns are made nullable BEFORE the policy writes NULLs
    assert upgrade_body.index("nullable=True") < upgrade_body.index("apply_flag_policy")


def test_migration_never_imports_application_code():
    """Frozen copies of the evidence rules and placeholder sets, by design: a
    migration must not change meaning when application code later evolves."""
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    assert "from app" not in source and "import app" not in source


def test_offline_sql_mode_is_refused_rather_than_silently_skipping_the_flag_policy(monkeypatch):
    monkeypatch.setattr(MIG.context, "is_offline_mode", lambda: True)
    with pytest.raises(RuntimeError, match="offline"):
        MIG.upgrade()


# --- an in-memory legacy database ------------------------------------------------


@pytest.fixture
def engine():
    engine = sa.create_engine("sqlite://")
    columns = ", ".join(f"{flag} BOOLEAN" for flag in FLAGS)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE products (barcode TEXT PRIMARY KEY, source TEXT NOT NULL, raw_ingredient_text TEXT NOT NULL, "
            "ingredient_ids TEXT NOT NULL DEFAULT '', is_verified BOOLEAN NOT NULL, health_score INTEGER, "
            f"allergens_detected TEXT NOT NULL, {columns})"
        )
        conn.exec_driver_sql(
            "CREATE TABLE ingredients (id TEXT PRIMARY KEY, common_name TEXT NOT NULL, scientific_name TEXT NOT NULL DEFAULT '', "
            "e_number TEXT, verification_status TEXT NOT NULL, source TEXT NOT NULL, "
            "identity_uncertain BOOLEAN NOT NULL DEFAULT 0, is_gluten BOOLEAN, is_lactose BOOLEAN, is_vegan BOOLEAN, "
            "is_vegetarian BOOLEAN, is_halal BOOLEAN, is_kosher BOOLEAN)"
        )
    yield engine
    engine.dispose()


def _ingredient(conn, id_, *, name=None, e_number=None, status="VERIFIED", source="CURATED_SEED", uncertain=False, **flags):
    values = {k: flags.get(k) for k in ("is_gluten", "is_lactose", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")}
    conn.execute(
        sa.text(
            "INSERT INTO ingredients (id, common_name, e_number, verification_status, source, identity_uncertain, is_gluten, "
            "is_lactose, is_vegan, is_vegetarian, is_halal, is_kosher) VALUES (:id, :name, :e_number, :status, :source, "
            ":uncertain, :is_gluten, :is_lactose, :is_vegan, :is_vegetarian, :is_halal, :is_kosher)"
        ),
        {"id": id_, "name": name or f"Name of {id_}", "e_number": e_number, "status": status, "source": source,
         "uncertain": uncertain, **values},
    )


def _product(conn, barcode, *, source, text, verified=False, score=0, allergens="None", ids="", **flags):
    conn.execute(
        sa.text(
            "INSERT INTO products (barcode, source, raw_ingredient_text, ingredient_ids, is_verified, health_score, "
            f"allergens_detected, {', '.join(FLAGS)}) VALUES (:barcode, :source, :text, :ids, :verified, :score, :allergens, "
            f"{', '.join(':' + f for f in FLAGS)})"
        ),
        {"barcode": barcode, "source": source, "text": text, "ids": ids, "verified": verified, "score": score,
         "allergens": allergens, **{f: flags.get(f) for f in FLAGS}},
    )


def _row(engine, barcode) -> dict:
    with engine.connect() as conn:
        return dict(conn.execute(sa.text("SELECT * FROM products WHERE barcode = :b"), {"b": barcode}).mappings().one())


def _run_policy(engine) -> int:
    with engine.begin() as conn:
        for statement in MIG.data_policy_statements():
            conn.exec_driver_sql(statement)
        return MIG.apply_flag_policy(conn)


def _known(row) -> dict:
    return {flag: row[flag] for flag in FLAGS if row[flag] is not None}


ALL_TRUE = {flag: True for flag in FLAGS}
ALL_FALSE = {flag: False for flag in FLAGS}


def _representative_legacy_rows(engine):
    with engine.begin() as conn:
        # 1. provider row: explicit tags (vegan True), defaults (rest False), text names wheat flour; verified, real score
        _product(conn, "off", source="open_food_facts", text="Wheat flour, sugar", verified=True, score=55,
                 is_vegan=True, is_gluten_free=False, is_lactose_free=False, is_vegetarian=False, is_halal=False, is_kosher=False)
        # 2. OCR-heuristic row on Bulgarian text: every flag a keyword-absence guess (True), unverified placeholder 0
        _product(conn, "bg-ocr", source="label_scan", text="Пшенично брашно, мляко, сол", verified=False, score=0, **ALL_TRUE)
        # 3. label row: "milk powder" supports not-vegan; lactose False was only a substring guess; gluten False default
        _product(conn, "en-label", source="label_scan_translated", text="MILK powder, sugar", verified=False, score=0,
                 is_vegan=False, is_lactose_free=False, is_gluten_free=False, is_vegetarian=True, is_halal=True, is_kosher=True)
        # 4. verified label row with a genuine computed 0 and pork evidence
        _product(conn, "verified-zero", source="label_scan", text="pork gelatin", verified=True, score=0, allergens="Soy",
                 is_halal=False, is_kosher=False, is_vegetarian=False, is_vegan=False, is_gluten_free=True, is_lactose_free=True)
        # 5. legacy 'local' row and an unrecognised source: unsupported Trues
        _product(conn, "local", source="local", text="water", verified=True, score=80, **ALL_TRUE)
        _product(conn, "weird", source="some_future_source", text="water", verified=False, score=0, **ALL_TRUE)
        # 6. provider row with everything False by default and no evidence at all
        _product(conn, "off-defaults", source="upcitemdb", text="water, salt", verified=False, score=0, **ALL_FALSE)
        # 7. allergen placeholder variants
        for i, allergens in enumerate([" none ", "N/A", "Null", "Milk, Soy", "", "-"]):
            _product(conn, f"alg{i}", source="local", text="water", verified=True, score=10, allergens=allergens)


def test_legacy_data_policy_keeps_evidence_and_resets_guesses(engine):
    _representative_legacy_rows(engine)
    _run_policy(engine)

    off = _row(engine, "off")
    assert _known(off) == {"is_vegan": True, "is_gluten_free": False}  # provider True kept; "wheat flour" supports the False
    assert off["is_lactose_free"] is None  # provider default False, no evidence -> unknown
    assert off["health_score"] == 55  # verified score untouched
    assert off["allergens_detected"] == ""

    bg = _row(engine, "bg-ocr")
    assert _known(bg) == {}  # every keyword-absence "True" guess invalidated
    assert bg["health_score"] is None  # unverified placeholder 0 -> unknown

    en = _row(engine, "en-label")
    assert _known(en) == {"is_vegan": False}  # "milk powder" is a dairy entry
    assert en["is_lactose_free"] is None  # a milk entry does not establish lactose: the legacy False was a guess
    assert en["is_gluten_free"] is None  # no gluten/wheat entry -> unsupported False reset

    zero = _row(engine, "verified-zero")
    assert zero["health_score"] == 0  # a GENUINE 0 on a verified row survives
    assert _known(zero) == {"is_halal": False, "is_kosher": False, "is_vegetarian": False, "is_vegan": False}  # "pork gelatin"
    assert zero["allergens_detected"] == "Soy"  # known positive allergen evidence preserved

    for barcode in ("local", "weird", "off-defaults"):
        assert _known(_row(engine, barcode)) == {}, barcode
    assert _row(engine, "local")["health_score"] == 80  # verified 'local' score kept
    assert _row(engine, "weird")["health_score"] is None

    assert [_row(engine, f"alg{i}")["allergens_detected"] for i in range(6)] == ["", "", "", "Milk, Soy", "", ""]


# --- the review's failure cases: a legacy false must be re-supported, not merely mentioned ---


# The previous policy, frozen for the record (verbatim keyword sets and `LIKE '%kw%'` semantics):
_OLD_POLICY_KEYWORDS = {
    "is_gluten_free": ("wheat", "gluten"),
    "is_lactose_free": ("milk", "whey", "lactose"),
    "is_vegan": ("pork", "gelatin", "milk"),
    "is_vegetarian": ("pork", "gelatin", "bacon"),
    "is_halal": ("pork", "alcohol"),
    "is_kosher": ("pork",),
}


def _old_policy_kept_false(flag: str, text: str) -> bool:
    return any(keyword in text.lower() for keyword in _OLD_POLICY_KEYWORDS[flag])


@pytest.mark.parametrize(
    "text, flags, old_policy_kept_it",
    [
        ("Gluten-free oat flour", ["is_gluten_free"], True),
        ("gluten free, lactose free", ["is_gluten_free", "is_lactose_free"], True),
        ("Free from milk", ["is_vegan", "is_lactose_free"], True),
        ("free of milk and gluten", ["is_vegan", "is_lactose_free", "is_gluten_free"], True),
        ("without wheat", ["is_gluten_free"], True),
        ("Free from: milk, soy, gluten, wheat", ["is_vegan", "is_lactose_free", "is_gluten_free"], True),
        ("May contain: milk, wheat, gluten", ["is_vegan", "is_lactose_free", "is_gluten_free"], True),
        ("coconut milk, water", ["is_vegan", "is_lactose_free"], True),
        ("Oat milk", ["is_vegan", "is_lactose_free"], True),
        ("almond milk, sugar", ["is_vegan", "is_lactose_free"], True),
        ("buttermilk", ["is_vegan", "is_lactose_free"], True),
        ("lactose-free milk", ["is_vegan", "is_lactose_free"], True),
        ("pork-free", ["is_vegetarian", "is_halal", "is_kosher"], True),
        # line-wrapped and free-list shapes (an OCR line wrap is whitespace, not a scope boundary):
        ("Gluten-\nfree oat flour", ["is_gluten_free"], True),
        ("Sugar. May contain\nmilk, soy", ["is_vegan", "is_lactose_free"], True),
        ("Free from:\nmilk, gluten", ["is_vegan", "is_lactose_free", "is_gluten_free"], True),
        ("Wheat, gluten and dairy free", ["is_gluten_free"], True),
        ("Milk/lactose free", ["is_vegan", "is_lactose_free"], True),
        ("Gluten: none", ["is_gluten_free"], True),
        ("Lactose (<0.01 g/100 g)", ["is_lactose_free"], True),
        ("Contains: no wheat, milk", ["is_gluten_free", "is_vegan"], True),
        # Already reset by the old policy (no English keyword at all); must stay unknown:
        ("Пшенично брашно, мляко", ["is_gluten_free", "is_vegan", "is_lactose_free"], False),
        ("", ["is_gluten_free", "is_vegan", "is_halal"], False),
    ],
)
def test_legacy_false_from_negated_ambiguous_or_substring_text_becomes_unknown(engine, text, flags, old_policy_kept_it):
    """The OLD policy kept each of these (the keyword is a substring of the
    text) as if it were a supported incompatibility. None of them is an
    ingredient occurrence, so each becomes unknown."""
    assert any(_old_policy_kept_false(flag, text) for flag in flags) is old_policy_kept_it

    with engine.begin() as conn:
        _product(conn, "row", source="label_scan", text=text, **{flag: False for flag in flags})
    _run_policy(engine)
    row = _row(engine, "row")
    assert all(row[flag] is None for flag in FLAGS), _known(row)


def test_genuine_occurrences_next_to_a_negated_phrase_are_not_discarded_globally(engine):
    with engine.begin() as conn:
        _product(conn, "a", source="label_scan", text="Gluten-free oats, wheat flour", is_gluten_free=False, is_vegan=False)
        _product(conn, "b", source="label_scan", text="Wheat flour, skimmed milk powder. Free from soy. May contain: eggs",
                 is_gluten_free=False, is_vegan=False, is_lactose_free=False)
        _product(conn, "c", source="label_scan", text="flavouring (may contain milk), pork, salt",
                 is_vegan=False, is_halal=False, is_kosher=False, is_lactose_free=False)
    _run_policy(engine)
    assert _known(_row(engine, "a")) == {"is_gluten_free": False}  # wheat flour kept; the negated/absent vegan guess reset
    assert _known(_row(engine, "b")) == {"is_gluten_free": False, "is_vegan": False}  # lactose still unsupported
    assert _known(_row(engine, "c")) == {"is_vegan": False, "is_halal": False, "is_kosher": False}  # pork; milk was precautionary


def test_supported_dairy_declarations_and_incompatibilities_are_retained(engine):
    with engine.begin() as conn:
        _product(conn, "dairy", source="label_scan", text="Contains: Milk, Soy", is_vegan=False, is_lactose_free=False)
        _product(conn, "lactose", source="label_scan", text="sugar, lactose", is_vegan=False, is_lactose_free=False)
        _product(conn, "bacon", source="label_scan", text="bacon, salt", is_vegetarian=False, is_vegan=False, is_halal=False)
        _product(conn, "mixed", source="label_scan", text="Пшенично брашно, wheat flour", is_gluten_free=False)
        _product(conn, "no-colon", source="label_scan", text="Contains milk and soy", is_vegan=False, is_lactose_free=False)
        _product(conn, "wrapped", source="label_scan", text="Wheat flour, sugar,\nmilk powder,\nsalt", is_gluten_free=False, is_vegan=False)
    _run_policy(engine)
    assert _known(_row(engine, "no-colon")) == {"is_vegan": False}  # "Contains" is a label word, not part of the entry
    assert _known(_row(engine, "wrapped")) == {"is_gluten_free": False, "is_vegan": False}
    assert _known(_row(engine, "dairy")) == {"is_vegan": False}
    assert _known(_row(engine, "lactose")) == {"is_vegan": False, "is_lactose_free": False}
    assert _known(_row(engine, "bacon")) == {"is_vegetarian": False, "is_vegan": False}  # bacon is not evidence for halal
    assert _known(_row(engine, "mixed")) == {"is_gluten_free": False}


def test_a_legacy_false_is_supported_by_a_linked_trusted_catalog_row_but_not_an_untrusted_one(engine):
    with engine.begin() as conn:
        _ingredient(conn, "curated_gluten", name="Barley Malt Extract", is_gluten=True)
        _ingredient(conn, "regulatory_pork", name="Porcine Collagen", source="REGULATORY_LOOKUP", is_vegetarian=False)
        _ingredient(conn, "eno", name="Some Additive", e_number="E999", is_halal=False)
        # every one of these carries the same definite booleans but is NOT trusted evidence:
        untrusted = {
            "ocr": dict(status="UNVERIFIED", source="OCR_HEURISTIC"),
            "limited": dict(status="LIMITED_DATA"),
            "uncertain": dict(uncertain=True),
            "unverified_seed": dict(status="UNVERIFIED"),
            "verified_ocr": dict(source="OCR_HEURISTIC"),  # VERIFIED status but an untrusted source
            "verified_gemini": dict(source="GEMINI"),
        }
        for key, overrides in untrusted.items():
            _ingredient(conn, f"u_{key}", name=f"Untrusted {key}", is_gluten=True, **overrides)
        _product(conn, "trusted", source="label_scan", text="Пшенично брашно, Barley Malt Extract", ids="synth_x, curated_gluten",
                 is_gluten_free=False, is_lactose_free=False)
        _product(conn, "trusted-veg", source="label_scan", text="Porcine Collagen", ids="regulatory_pork",
                 is_vegetarian=False, is_vegan=False, is_halal=False)
        _product(conn, "trusted-enumber", source="label_scan", text="Colour (E 999)", ids="eno", is_halal=False)
        for key in untrusted:
            _product(conn, f"untrusted-{key}", source="label_scan", text=f"Untrusted {key}, wheat starch", ids=f"u_{key}",
                     is_gluten_free=False)
        _product(conn, "missing-row", source="label_scan", text="Пшенично брашно", ids="does_not_exist", is_gluten_free=False)
        # linked but NOT named by any exact entry: the substring link is not identity
        _product(conn, "linked-not-named", source="label_scan", text="Пшенично брашно, sugar", ids="curated_gluten", is_gluten_free=False)
        _product(conn, "named-in-negated-scope", source="label_scan", text="May contain: Barley Malt Extract", ids="curated_gluten",
                 is_gluten_free=False)
    _run_policy(engine)
    assert _known(_row(engine, "trusted")) == {"is_gluten_free": False}  # independently supported; lactose is not
    assert _known(_row(engine, "trusted-veg")) == {"is_vegetarian": False, "is_vegan": False}  # not vegetarian => not vegan
    assert _known(_row(engine, "trusted-enumber")) == {"is_halal": False}  # named by its E-number entry
    for key in untrusted:
        assert _known(_row(engine, f"untrusted-{key}")) == {}, key  # a bool on an untrusted row supports nothing
    for barcode in ("missing-row", "linked-not-named", "named-in-negated-scope"):
        assert _known(_row(engine, barcode)) == {}, barcode


def test_a_trusted_row_reached_only_through_a_substring_link_is_not_evidence(engine):
    """The catalog matcher links "coconut milk" to a trusted row named "Milk"; the legacy False
    that resulted must not be re-supported by that link."""
    with engine.begin() as conn:
        _ingredient(conn, "milk_row", name="Milk", is_lactose=True, is_vegan=False)
        _product(conn, "coconut", source="label_scan", text="Coconut milk, water", ids="milk_row", is_lactose_free=False, is_vegan=False)
        _product(conn, "real", source="label_scan", text="Water, Milk", ids="milk_row", is_lactose_free=False, is_vegan=False)
    _run_policy(engine)
    assert _known(_row(engine, "coconut")) == {}
    assert _known(_row(engine, "real")) == {"is_lactose_free": False, "is_vegan": False}


def test_a_provider_true_survives_only_when_real_evidence_does_not_contradict_it(engine):
    with engine.begin() as conn:
        _ingredient(conn, "curated_gluten", name="Barley Malt Extract", is_gluten=True)
        _product(conn, "kept", source="open_food_facts", text="rice, oat milk", ids="", is_vegan=True, is_gluten_free=True)
        _product(conn, "contradicted", source="open_food_facts", text="skimmed milk powder, wheat flour",
                 is_vegan=True, is_gluten_free=True, is_halal=True)
        _product(conn, "catalog-contradicted", source="gs1_digital_link", text="Barley Malt Extract", ids="curated_gluten", is_gluten_free=True)
        _product(conn, "implied", source="open_food_facts", text="bacon", is_vegan=True, is_vegetarian=False)
        _product(conn, "not-provider", source="label_scan", text="rice", is_vegan=True)
    _run_policy(engine)
    assert _known(_row(engine, "kept")) == {"is_vegan": True, "is_gluten_free": True}  # "oat milk" contradicts nothing
    assert _known(_row(engine, "contradicted")) == {"is_halal": True}  # conflicting real evidence -> unknown, not True
    assert _known(_row(engine, "catalog-contradicted")) == {}
    assert _known(_row(engine, "implied")) == {"is_vegetarian": False}  # vegan True vs bacon: never left as True
    assert _known(_row(engine, "not-provider")) == {}


def test_policy_never_turns_uncertainty_into_true_and_only_touches_flags(engine):
    _representative_legacy_rows(engine)
    with engine.begin() as conn:
        _product(conn, "extra", source="label_scan", text="milk, wheat, pork, Пшенично", is_vegan=False, is_gluten_free=False)
    before = {r["barcode"]: r for r in map(dict, engine.connect().execute(sa.text("SELECT * FROM products")).mappings())}
    _run_policy(engine)
    after = {r["barcode"]: r for r in map(dict, engine.connect().execute(sa.text("SELECT * FROM products")).mappings())}
    assert before.keys() == after.keys()  # no row deleted or added
    for barcode, row in after.items():
        for flag in FLAGS:
            if row[flag] is True:
                assert before[barcode][flag] is True and before[barcode]["source"] in MIG._PROVIDER_SOURCES, (barcode, flag)
        for column in ("source", "raw_ingredient_text", "ingredient_ids", "is_verified"):
            assert row[column] == before[barcode][column], (barcode, column)  # links and text untouched


def test_legacy_data_policy_is_idempotent(engine):
    _representative_legacy_rows(engine)
    with engine.begin() as conn:
        _ingredient(conn, "curated_gluten", name="Barley Malt Extract", is_gluten=True)
        _product(conn, "linked", source="label_scan", text="coconut milk, Barley Malt Extract", ids="curated_gluten", is_gluten_free=False, is_vegan=False)
    assert _run_policy(engine) > 0
    with engine.connect() as conn:
        once = conn.execute(sa.text("SELECT * FROM products ORDER BY barcode")).fetchall()
    assert _run_policy(engine) == 0  # a second pass changes nothing
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT * FROM products ORDER BY barcode")).fetchall() == once


def test_the_frozen_evidence_rules_agree_with_todays_application_rules_on_a_corpus():
    """Not a runtime dependency (the migration imports nothing from `app`):
    a tripwire that the frozen copy was taken from the application rules
    faithfully. If the application rules are intentionally changed later, the
    historical migration keeps its meaning -- update this corpus, not the
    migration."""
    from app.services import dietary_suitability as ds

    corpus = [
        "coconut milk", "oat milk", "milk", "skimmed milk powder", "whey powder", "lactose", "gluten-free oats, wheat flour",
        "Free from: milk, soy, gluten", "May contain: milk, wheat", "Contains: Milk, Soy", "pork gelatin", "bacon", "gelatin",
        "Wheat Flour (with Calcium), Sugar", "Пшенично брашно, мляко", "", "flavouring (may contain milk), wheat flour",
        "Ingredients: wheat flour, milk. Free from soy.", "alcohol, ethanol", "buttermilk, milk chocolate",
        "Gluten-\nfree oat flour", "Sugar. May contain\nmilk, soy", "Wheat, gluten and dairy free", "Milk/lactose free",
        "Gluten: none", "Lactose (<0.01 g/100 g)", "Milk (0%)", "Contains: no wheat, milk", "Contains milk and soy",
        "Wheat flour, sugar,\nmilk powder,\nsalt", "May contain traces of nuts, e.g. milk, soy", "cow's milk, sweet whey powder",
        "beef gelatin", "Made in Italy, milk", "Wheat and rye flour", "1" * 5000, "milk, " * 3000,
    ]
    for text in corpus:
        assert MIG.text_supported_false_flags(text) == set(ds.derive_text_evidence(text).flags), text


def test_downgrade_backfill_leaves_no_nulls_so_not_null_can_be_restored(engine):
    _representative_legacy_rows(engine)
    _run_policy(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM products WHERE health_score IS NULL")).scalar() > 0
    with engine.begin() as conn:
        for statement in MIG.downgrade_backfill_statements():
            conn.exec_driver_sql(statement)
    with engine.connect() as conn:
        for column in (*FLAGS, "health_score"):
            assert conn.execute(sa.text(f"SELECT COUNT(*) FROM products WHERE {column} IS NULL")).scalar() == 0, column
    # backfill values: unknown flag -> false, unscored -> the old 0 placeholder; allergens stay ""
    bg = _row(engine, "bg-ocr")
    assert all(bg[flag] == 0 for flag in FLAGS) and bg["health_score"] == 0
    assert _row(engine, "off")["is_vegan"] == 1  # a kept provider True is not touched by the backfill
    assert _row(engine, "alg0")["allergens_detected"] == ""


def test_the_migration_policy_is_linear_on_adversarial_text():
    """The percent regex once rescanned a whole digit run from every start position (15 s for 20 KB)."""
    import time

    for text in ("1" * 30_000, "1" * 15_000 + " " * 15_000, ", " * 100_000, "milk, " * 50_000, "0%" * 100_000):
        started = time.perf_counter()
        MIG.text_supported_false_flags(text)
        assert time.perf_counter() - started < 3.0
