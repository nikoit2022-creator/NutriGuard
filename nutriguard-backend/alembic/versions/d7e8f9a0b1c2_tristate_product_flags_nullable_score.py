"""tri-state product dietary flags, nullable Health Score, honest allergens

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1

Issue #21 follow-up ("truthful unknown values"). Three related schema/data
changes on `products`, all so that "we do not know" stops being encoded as
a fabricated value:

1. `is_gluten_free` / `is_lactose_free` / `is_vegan` / `is_vegetarian` /
   `is_halal` / `is_kosher` become NULLABLE, tri-state:
   NULL = unknown/insufficient evidence, false = SUPPORTED incompatibility,
   true = SUPPORTED suitability. Same convention as the per-ingredient
   columns made nullable by `f5a6b7c8d9e0`.
2. `health_score` becomes NULLABLE: NULL = no Health Score available
   (product not verified). A stored 0 stops doubling as a placeholder.
3. `allergens_detected` keeps its type (NOT NULL text) but the literal
   placeholder "None" -- which read as an allergen-absence guarantee and
   was produced by an incomplete (soy/milk-only) heuristic -- is
   rewritten to "" (unknown / none detected).

LEGACY-DATA POLICY (documented, deliberately conservative). Before this
migration nothing recorded whether a stored flag was EVIDENCE or a GUESS,
and the same value could come from either:
  * `true` was written by (a) a barcode provider's explicit structured tag
    (evidence), or (b) the label/OCR keyword heuristic / a label-extraction
    default when no English keyword happened to match (a GUESS -- absence
    of a keyword is not suitability; wrong for Bulgarian/mixed/empty text).
  * `false` was written by (a) a keyword HIT, (b) a provider/label-
    extraction default meaning only "not stated" (unknown), or (c) an
    explicit provider/model `false`. A keyword "hit" was a SUBSTRING match
    ("milk" inside "coconut milk", "gluten" inside "gluten-free", and
    everything after "free from:" / "may contain:"), so a stored `false`
    is NOT evidence merely because the row's text contains the keyword.
The `false` in the new contract means SUPPORTED incompatibility, so a legacy
`false` survives only when it can be demonstrated again from stored data,
with the SAME evidence rules the application now uses (frozen below; this
migration never imports application code):

  * `false` is KEPT only when it is supported by (1) EXACT ingredient-entry
    identity in the row's stored `raw_ingredient_text` -- the text is split
    into ingredient entries and an entry counts only when it is exactly one
    of a small, closed set of unambiguous identities ("milk", "skimmed milk
    powder", "whey", "lactose", "pork", "gelatin", "bacon", "wheat flour",
    "gluten", ...), outside any precautionary/negating header ("may
    contain:", "free from:", "produced in a facility that handles:"),
    whose scope ends at the sentence / parenthesis group -- so a genuine
    "wheat flour" next to "gluten-free oats" is kept and "coconut milk",
    "gluten-free" (also line-wrapped), "free from milk", "wheat, gluten and
    dairy free", "Gluten: none", "without wheat", "buttermilk", Bulgarian
    and empty text support nothing; or (2) a linked TRUSTED catalog row
    (`products.ingredient_ids` -> `ingredients` rows that are VERIFIED and
    from CURATED_SEED/REGULATORY_LOOKUP) that an exact ingredient entry of
    the stored text NAMES (name or E-number; the stored link came from the
    application's substring matcher, which is not identity) and whose own
    flag says it contains gluten/lactose or is not vegan/vegetarian/halal/
    kosher. Otherwise it is
    reset to NULL (unknown). A milk entry alone does NOT support
    `is_lactose_free = false` (lactose-free milk is still milk); halal /
    kosher are supported only by pork or a trusted catalog row.
  * `true` is KEPT only for rows sourced from a barcode provider
    ('open_food_facts', 'gs1_digital_link', 'upcitemdb') AND not contradicted
    by the same real evidence above (two conflicting claims support neither,
    exactly as at runtime); for every other source ('local', 'label_scan',
    'label_scan_translated', anything unrecognised) it is reset to NULL --
    an unsupported positive claim is never preserved. Never a `true` from
    uncertainty.
  * PROVENANCE LIMITATION: an explicit provider/model `false` whose support
    is not present in the stored text or linked catalog rows cannot be told
    apart from a default `false` and becomes unknown; a rediscovery / label
    re-scan restores it under the new evidence-gated logic. Conversely
    `products.source` is only rewritten when an enrichment completes an
    evidence group, so a provider-sourced row whose flags were later
    overwritten by an old-code label scan can keep a guessed `true`.
  * `health_score` is reset to NULL for every row that is not
    `is_verified` (its value there was only ever a placeholder 0); a
    verified row's score, including a genuine 0, is untouched.
  * `allergens_detected`: the literal placeholders ("None", "N/A", ...) are
    rewritten to "". Positive allergen names are NOT rewritten: a stored
    "Milk"/"Soy" on a non-provider row may have come from the old substring
    heuristic or from a real structured declaration and stored data cannot
    tell which; over-reporting an allergen is never an absence claim, and a
    re-scan repopulates it under the new logic.
  * Trustworthy data is not indiscriminately destroyed: provider `true`s not
    contradicted by evidence, evidence-supported `false`s, every verified
    score and known allergens all survive.

Scope: the policy is evaluated in Python (a portable SQL `LIKE` cannot apply
occurrence-level negation/scope), so `alembic upgrade --sql` (offline mode)
is not supported for this revision and raises. It is deterministic and
idempotent.

DOWNGRADE is LOSSY by necessity (documented): the pre-migration schema is
NOT NULL, so NULL flags are backfilled to `false` (the least-asserting
boolean, and exactly the value the previous provider path used for
"unknown") and a NULL score is backfilled to 0 (its previous placeholder).
The unknown/known distinction is lost for those rows, and the allergen
"None" placeholder is NOT restored (an empty string is a valid pre-migration
value). Consequently upgrade -> downgrade -> upgrade is not an identity on
data written after the first upgrade: re-running the policy above resets
non-provider `true`s again, and re-evaluates every backfilled `false` with
the same evidence rules (an unsupported one returns to NULL; one that the
stored text/catalog genuinely supports is a supported `false`). Take a
backup before running either direction against real data.
"""

import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from alembic import context, op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None

_PROVIDER_SOURCES = ("open_food_facts", "gs1_digital_link", "upcitemdb")
_TRUSTED_CATALOG_SOURCES = ("CURATED_SEED", "REGULATORY_LOOKUP")

FLAGS = ("is_gluten_free", "is_lactose_free", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")

# Frozen copy of `app.services.barcode_text_safety._PLACEHOLDER_VALUES`.
_PLACEHOLDER_ALLERGEN_VALUES = ("", "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown")

# --- frozen evidence rules ---------------------------------------------------
# A FROZEN copy of the ingredient-entry identity rules in
# `app.services.dietary_suitability` at the time of this revision (verbatim,
# including its tokenizer, scope handling and the length bound). It must not
# track later changes to application code: a historical migration keeps the
# meaning it shipped with.
# Ingredient-entry identities (FROZEN copy of app.services.dietary_suitability at this revision) (matched with `fullmatch` against ONE
# normalized ingredient entry, never searched inside a longer text). Each
# is a food that is animal-derived / gluten-containing / soy by definition
# once it stands alone as an ingredient. Every deliberate omission is
# listed in the module docstring; do not add a pattern without a source
# that makes the identity unambiguous, and never add a plant-milk exclusion
# list instead of tightening the pattern.
_MILK = re.compile(
    r"(?:(?:whole|skimmed|skim|semi skimmed|dried|dry|powdered|full cream|pasteurised|pasteurized|"
    r"cow'?s?|condensed|evaporated)\s+)*milk(?:\s+(?:powder|solids|protein|proteins|fat))?"
)
_WHEY = re.compile(r"(?:sweet\s+)?whey(?:\s+(?:powder|protein))?")
_LACTOSE = re.compile(r"lactose|milk sugar")
_PORK = re.compile(r"pork(?:\s+(?:meat|fat|gelatin|gelatine))?")
_BACON = re.compile(r"bacon")
_GELATIN = re.compile(r"(?:(?:beef|bovine|fish)\s+)?gelatine?")
_GLUTEN_GRAIN = re.compile(
    r"gluten|(?:(?:whole|wholemeal|whole meal|durum)\s+)?wheat(?:\s+(?:flour|semolina|bran|germ|gluten))?"
)
_SOY = re.compile(r"(?:soy|soya|soja)(?:\s?beans?)?(?:\s+(?:flour|protein|lecithin|sauce|milk|powder))?")

# (identity, flags it supports as `False`, positive allergens it declares)
_ENTRY_EVIDENCE: tuple[tuple["re.Pattern[str]", frozenset[str], frozenset[str]], ...] = (
    (_MILK, frozenset({"is_vegan"}), frozenset({"Milk"})),
    (_WHEY, frozenset({"is_vegan"}), frozenset({"Milk"})),
    (_LACTOSE, frozenset({"is_lactose_free", "is_vegan"}), frozenset({"Milk"})),
    (_PORK, frozenset({"is_vegetarian", "is_vegan", "is_halal", "is_kosher"}), frozenset()),
    (_BACON, frozenset({"is_vegetarian", "is_vegan"}), frozenset()),
    (_GELATIN, frozenset({"is_vegetarian", "is_vegan"}), frozenset()),
    (_GLUTEN_GRAIN, frozenset({"is_gluten_free"}), frozenset()),
    (_SOY, frozenset(), frozenset({"Soy"})),
)

# Allergen names in the (stable) order `detect_allergens_text` reports them.
_ALLERGEN_ORDER: tuple[str, ...] = ("Soy", "Milk")

# Hard bound on the text examined. A real ingredient list is a few hundred
# characters; the bound only exists so that adversarial input cannot make a
# scan (or a migration) slow. Truncation can only DROP evidence (it never
# turns a scoped-out entry into an evidential one).
_MAX_TEXT_CHARS = 100_000

# Phrases that open a precautionary ("may contain", cross-contact, shared
# facility) or negating ("free from", "without", "contains no", "does not
# contain", "none of") statement. Everything after such a phrase in the same
# sentence / parenthesis group is a statement ABOUT the product, not an
# ingredient occurrence. Adjectival "gluten-free" / "no artificial colours"
# inside a single entry deliberately do not open a scope (they cannot match
# an identity anyway and must not hide genuine entries after them).
_SCOPE_MARKER_RE = re.compile(
    r"\b(?:may|might|could)\s+(?:also\s+)?contain\b|\btraces?\s+of\b|\bcross\s+contact\b"
    r"|\b(?:facility|factory|premises|equipment|bakery|production\s+line)\b"
    r"|\bfree\s+(?:from|of)\b|\bwithout\b|\bcontains?\s+(?:no|none)\b|\bnone\s+of\b|\bneither\b"
    r"|\bdoes(?:n't|\s+not)\s+contain\b|\bnot\s+contain(?:ing)?\b"
)
# Label words that may directly precede a "no X" list ("Contains: no wheat, milk").
_CONTAINS_HEADERS = frozenset({"contains", "contain", "allergens", "allergen", "allergen information", "allergy advice"})
_NEGATION_START_RE = re.compile(r"^(?:no|non|none|not|neither|nor)\b")
_FREE_END_RE = re.compile(r"\bfree$")
# A quantity qualifier right after an entry -- "gluten (<20 ppm)", "milk (0%)" -- makes the
# entry a nutrient/claim line, not an ingredient occurrence.
_QUANTITY_QUALIFIER_RE = re.compile(r"^\s*(?:[<>\u2264\u2265]|0+(?:[.,]0+)?(?:\s|$))")
_AND_SPLIT_RE = re.compile(r"\s+(?:and|&|or)\s+")
_SENTENCE_BREAK_RE = re.compile(r"(?<!\d)\.(?!\d)|[!?]+")
_TOKEN_RE = re.compile(r"[()\[\]]|[,;:/|]|[^,;:/|()\[\]]+")
_PERCENT_RE = re.compile(r"(?<![\d.,])(\d+(?:[.,]\d+)?)\s*%")
_ZERO_RE = re.compile(r"0+(?:[.,]0+)?")
_NEWLINE_RE = re.compile(r"[\r\n]+")
_DASH_RE = re.compile(r"[-\u2010-\u2015_]+")
_DECORATION_RE = re.compile(r"[*\u2020\u2021\"\u201c\u201d`]")
_LEADING_FILLER_RE = re.compile(r"^(?:(?:and|&)\s+)?(?:(?:ingredients?|contains?)\s+)?")


@dataclass(frozen=True)
class TextEvidence:
    """What raw ingredient text SUPPORTS via entry identity: flags with a
    supported incompatibility (`False`) and positively declared allergens.
    Empty means "unknown", never "suitable" / "allergen-free"."""

    flags: frozenset[str] = frozenset()
    allergens: frozenset[str] = frozenset()


def _normalize_entry(chunk: str) -> str:
    entry = _DECORATION_RE.sub("", _DASH_RE.sub(" ", chunk))
    return re.sub(r"\s+", " ", entry).strip()


def _percent_replacement(match: "re.Match[str]") -> str:
    # A real quantity ("milk 3%") is dropped; a zero quantity ("milk (0%)") is kept as "0"
    # so the entry is recognised as a nutrient/claim line.
    return " 0 " if _ZERO_RE.fullmatch(match.group(1)) else " "


def _prepare(raw_text: str) -> str:
    text = raw_text[:_MAX_TEXT_CHARS].lower().replace("\u2019", "'")
    text = _NEWLINE_RE.sub(" ", text)  # a line wrap ("gluten-<newline>free") is whitespace, never a sentence/scope boundary
    text = _PERCENT_RE.sub(_percent_replacement, text)
    text = re.sub(r"\be\.g\.?", "eg", text)  # abbreviations must not end a sentence (and its scope)
    text = re.sub(r"\bi\.e\.?", "ie", text)
    return re.sub(r"\b(?:max|min|approx|ca|etc|vs)\.", lambda m: m.group(0)[:-1], text)


class _Record:
    __slots__ = ("chunk", "depth", "delimiter", "negated", "ends_free")

    def __init__(self, chunk: str, depth: int, delimiter: str, negated: bool) -> None:
        self.chunk = chunk
        self.depth = depth
        self.delimiter = delimiter
        self.negated = negated
        self.ends_free = bool(_FREE_END_RE.search(chunk))


def _sentence_entries(sentence: str) -> Iterator[str]:
    tokens = _TOKEN_RE.findall(sentence)
    records: list[_Record] = []
    depth = 0
    scope_depth: int | None = None
    previous = ""
    for index, token in enumerate(tokens):
        if token in ("(", "["):
            depth += 1
            continue
        if token in (")", "]"):
            depth = max(depth - 1, 0)
            if scope_depth is not None and depth < scope_depth:
                scope_depth = None
            continue
        if token in (",", ";", ":", "/", "|"):
            continue
        chunk = _normalize_entry(token)
        if not chunk:
            continue
        opens_scope = _SCOPE_MARKER_RE.search(chunk) or (
            previous in _CONTAINS_HEADERS and _NEGATION_START_RE.match(chunk)
        )
        previous = chunk
        if scope_depth is None and opens_scope:
            scope_depth = depth
            continue
        if scope_depth is not None:
            continue
        delimiter = tokens[index + 1] if index + 1 < len(tokens) else ""
        quantified = delimiter in ("(", "[") and index + 2 < len(tokens) and _QUANTITY_QUALIFIER_RE.match(tokens[index + 2])
        label = delimiter == ":"  # "Gluten: none", "Lactose: 0.0 g" -- a key, not an ingredient
        negated = bool(quantified or label or _NEGATION_START_RE.match(chunk) or _FREE_END_RE.search(chunk))
        records.append(_Record(chunk, depth, delimiter, negated))

    # "Wheat, gluten and dairy free": the trailing "free" negates the bare nouns before it.
    for position, record in enumerate(records):
        if not record.ends_free:
            continue
        before = position - 1
        while (
            before >= 0
            and not records[before].negated
            and records[before].depth == record.depth
            and records[before].delimiter in (",", "/", "|")
            and " " not in records[before].chunk
        ):
            records[before].negated = True
            before -= 1

    for record in records:
        if record.negated:
            continue
        for part in _AND_SPLIT_RE.split(_LEADING_FILLER_RE.sub("", record.chunk, count=1)):
            if part:
                yield part


def _evidential_entries(raw_text: str) -> Iterator[str]:
    """The normalized ingredient entries of `raw_text` that are genuine
    occurrences: not under a precautionary/negating header (scoped to the
    sentence, or to the parenthesis group the header appeared in), not the
    subject of a "... free" / "no ..." / "key: value" / quantity line."""
    for sentence in _SENTENCE_BREAK_RE.split(_prepare(raw_text)):
        yield from _sentence_entries(sentence)


def derive_text_evidence(raw_text: str | None) -> TextEvidence:
    """Supported incompatibilities / positive allergens in `raw_text`,
    from exact ingredient-entry identity only (see the module docstring).
    Substring hits, plant-based/derived names, negations, precautionary
    statements and non-English text yield nothing (unknown)."""
    return _evidence_from_entries(_evidential_entries(raw_text) if raw_text and raw_text.strip() else ())


def _evidence_from_entries(entries: Iterable[str]) -> TextEvidence:
    flags: set[str] = set()
    allergens: set[str] = set()
    for entry in entries:
        for pattern, entry_flags, entry_allergens in _ENTRY_EVIDENCE:
            if pattern.fullmatch(entry):
                flags |= entry_flags
                allergens |= entry_allergens
    return TextEvidence(frozenset(flags), frozenset(allergens))


def _is_named_by_entries(ingredient: Any, entries: Iterable[str]) -> bool:
    """True when an exact ingredient ENTRY of the text names this catalog row
    (its common/scientific name, or its E-number). The upstream catalog matcher
    links a token to a row by bidirectional SUBSTRING ("coconut milk" -> a row
    named "Milk"), which is not identity; without an exact entry the row's flags
    are not evidence about THIS product."""
    names = set()
    for attribute in ("common_name", "scientific_name"):
        value = getattr(ingredient, attribute, None)
        if isinstance(value, str) and value.strip():
            lowered = value.lower()
            names.add(_normalize_entry(lowered))
            names.add(_normalize_entry(re.sub(r"\s*\([^)]*\)", "", lowered)))
    names.discard("")
    e_number = getattr(ingredient, "e_number", None)
    e_key = re.sub(r"\s+", "", e_number.lower()) if isinstance(e_number, str) and e_number.strip() else None
    for entry in entries:
        if entry in names or (e_key is not None and re.sub(r"\s+", "", entry) == e_key):
            return True
    return False

def _sql_list(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def text_supported_false_flags(raw_text) -> set[str]:
    """Flags whose incompatibility is SUPPORTED by exact ingredient-entry
    identity in `raw_text` (never a substring, never inside a negating or
    precautionary header)."""
    return set(derive_text_evidence(raw_text).flags)


def _tri_state(value):
    """NULL stays unknown; anything else is a boolean (a driver may hand back
    0/1 for a BOOLEAN column, e.g. SQLite through a plain-text query)."""
    return None if value is None else bool(value)


def catalog_supported_false_flags(ingredient: Mapping) -> set[str]:
    """Product flags a TRUSTED catalog row's own tri-state flags support as
    `false` (`is_gluten`/`is_lactose` mean "contains")."""
    flag = {name: _tri_state(ingredient.get(name)) for name in (
        "is_gluten", "is_lactose", "is_vegan", "is_vegetarian", "is_halal", "is_kosher")}
    found: set[str] = set()
    if flag["is_gluten"] is True:
        found.add("is_gluten_free")
    if flag["is_lactose"] is True:
        found.add("is_lactose_free")
    if flag["is_vegan"] is False:
        found.add("is_vegan")
    if flag["is_vegetarian"] is False:
        found |= {"is_vegetarian", "is_vegan"}
    if flag["is_halal"] is False:
        found.add("is_halal")
    if flag["is_kosher"] is False:
        found.add("is_kosher")
    return found


class _Named:
    """Attribute view of a catalog row for `_is_named_by_entries`."""

    def __init__(self, row: Mapping) -> None:
        self.common_name = row.get("common_name")
        self.scientific_name = row.get("scientific_name")
        self.e_number = row.get("e_number")


def resolve_legacy_flags(
    *, source, raw_text, stored: Mapping, linked_catalog: Iterable[Mapping] = ()
) -> dict:
    """The post-migration value of the six flags for one legacy row (see the
    module docstring for the policy). `stored` holds the legacy booleans;
    `linked_catalog` the TRUSTED catalog rows the product links to."""
    entries = list(_evidential_entries(raw_text)) if raw_text and raw_text.strip() else []
    supported_false = set(_evidence_from_entries(entries).flags)
    for row in linked_catalog:
        # Only a row an exact ingredient entry NAMES is evidence about this product.
        if _is_named_by_entries(_Named(row), entries):
            supported_false |= catalog_supported_false_flags(row)
    provider = source in _PROVIDER_SOURCES
    resolved = {}
    for flag in FLAGS:
        value = _tri_state(stored.get(flag))
        if value is False:
            resolved[flag] = False if flag in supported_false else None
        elif value is True:
            resolved[flag] = True if (provider and flag not in supported_false) else None
        else:
            resolved[flag] = None
    if resolved["is_vegetarian"] is False and resolved["is_vegan"] is True:
        resolved["is_vegan"] = None  # vegan implies vegetarian; the two claims conflict
    return resolved


def data_policy_statements() -> list[str]:
    """The SQL half of the legacy-data policy (see the module docstring), as
    plain, portable SQL in execution order: unverified placeholder scores and
    allergen placeholders. The flag policy needs per-occurrence text
    evaluation and lives in `apply_flag_policy`."""
    return [
        "UPDATE products SET health_score = NULL WHERE is_verified = false",
        "UPDATE products SET allergens_detected = '' "
        f"WHERE lower(trim(allergens_detected)) IN ({_sql_list(_PLACEHOLDER_ALLERGEN_VALUES)})",
    ]


def apply_flag_policy(connection) -> int:
    """Applies `resolve_legacy_flags` to every product row; returns the
    number of rows whose flags changed. Deterministic and idempotent."""
    trusted = {}
    catalog_query = (
        "SELECT id, common_name, scientific_name, e_number, is_gluten, is_lactose, is_vegan, is_vegetarian, "
        "is_halal, is_kosher FROM ingredients "
        f"WHERE verification_status = 'VERIFIED' AND CAST(source AS TEXT) IN ({_sql_list(_TRUSTED_CATALOG_SOURCES)}) "
        "AND identity_uncertain = false"
    )
    for row in connection.execute(sa.text(catalog_query)).mappings():
        if catalog_supported_false_flags(row):
            trusted[row["id"]] = dict(row)

    columns = ", ".join(FLAGS)
    rows = connection.execute(
        sa.text(f"SELECT barcode, source, raw_ingredient_text, ingredient_ids, {columns} FROM products")
    ).mappings().all()
    updates = []
    for row in rows:
        linked = [trusted[i.strip()] for i in (row["ingredient_ids"] or "").split(",") if i.strip() in trusted]
        stored = {flag: _tri_state(row[flag]) for flag in FLAGS}
        resolved = resolve_legacy_flags(
            source=row["source"], raw_text=row["raw_ingredient_text"] or "", stored=stored, linked_catalog=linked
        )
        if resolved != stored:
            updates.append({"barcode": row["barcode"], **resolved})
    if updates:
        assignments = ", ".join(f"{flag} = :{flag}" for flag in FLAGS)
        connection.execute(sa.text(f"UPDATE products SET {assignments} WHERE barcode = :barcode"), updates)
    return len(updates)


def downgrade_backfill_statements() -> list[str]:
    """Backfills that must run BEFORE the NOT NULL constraints are restored."""
    statements = [f"UPDATE products SET {column} = false WHERE {column} IS NULL" for column in FLAGS]
    statements.append("UPDATE products SET health_score = 0 WHERE health_score IS NULL")
    return statements


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "d7e8f9a0b1c2 evaluates its legacy-data policy in Python against live rows; "
            "offline (--sql) mode is not supported for this revision."
        )
    op.alter_column("products", "health_score", existing_type=sa.Integer(), nullable=True)
    for column in FLAGS:
        op.alter_column("products", column, existing_type=sa.Boolean(), nullable=True)

    for statement in data_policy_statements():
        op.execute(statement)
    apply_flag_policy(op.get_bind())


def downgrade() -> None:
    for statement in downgrade_backfill_statements():
        op.execute(statement)
    for column in FLAGS:
        op.alter_column("products", column, existing_type=sa.Boolean(), nullable=False)
    op.alter_column("products", "health_score", existing_type=sa.Integer(), nullable=False)
    # allergens_detected: intentionally NOT restored to "None" (lossy, documented).
