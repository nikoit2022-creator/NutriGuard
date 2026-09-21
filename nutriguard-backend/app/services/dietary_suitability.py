"""
Evidence-backed, tri-state PRODUCT-level dietary suitability.

Contract (the same one `Product`/`ProductOut` document): for every one of
`is_gluten_free`/`is_lactose_free`/`is_vegan`/`is_vegetarian`/`is_halal`/
`is_kosher`

    None  = unknown / insufficient evidence
    False = SUPPORTED incompatibility (positive evidence the product does
            not meet the requirement)
    True  = SUPPORTED suitability (an explicit claim from a source that
            actually states it -- a provider's structured tag, or a
            structured label extraction)

The ABSENCE of a keyword is never evidence of suitability, and the
PRESENCE of a substring is never evidence of an ingredient. Earlier code
computed e.g. `is_vegan = not ("pork" in text or "gelatin" in text or
"milk" in text)` (suitable because no English word matched) and later
`"milk" in text` -> not vegan / not lactose-free / allergen "Milk"
(so "coconut milk" was a dairy product). Both are the same mistake: a
mention is not an identity. This module therefore NEVER derives `True`
from text or from a matched ingredient list, and derives `False` / a
positive allergen from raw text ONLY through the two narrow routes below.

Evidence that may support `False` (or a positive allergen):

  1. Explicit source values (a provider's structured tags, a structured
     label extraction's JSON booleans) -- see `resolve_flags`'s `explicit`.
  2. Trusted catalog evidence: a matched ingredient row that is itself
     evidence -- `VERIFIED` and from a curated/regulatory source (see
     `_is_trusted_catalog_row`) -- that an exact ingredient ENTRY of the text
     names (common/scientific name or E-number; the upstream matcher's
     substring link "coconut milk" -> "Milk" is not identity), and whose own
     tri-state flag says it contains gluten/lactose or is not vegan/
     vegetarian/halal/kosher. An UNVERIFIED / OCR- or Gemini-observed /
     unknown-provenance row contributes nothing, whatever booleans it holds.
  3. Raw-text ENTRY IDENTITY (`derive_text_evidence`): the text is split
     into ingredient entries and an entry counts only when it is EXACTLY a
     small, closed set of unambiguous ingredient identities ("milk",
     "skimmed milk powder", "whey", "lactose", "pork", "gelatin",
     "wheat flour", "gluten", "soy lecithin", ...). Anything else is
     "unknown": "coconut milk", "oat milk", "buttermilk", "gluten-free
     wheat starch", "no milk" are not entries of that set, so nothing
     needs a negation or a plant-milk blacklist to stay out. Entries
     under a precautionary or negating header ("may contain: ...",
     "free from: ...", "produced in a facility that handles: ...") are not
     ingredient occurrences either; that scope ends at the sentence (or
     the parenthesis group) so a genuine occurrence elsewhere in the same
     text is never discarded globally. Deliberately NOT inferred from
     text: lactose from a "milk" entry (a lactose-free milk is still
     milk), halal/kosher from anything but pork (alcohol, gelatin source
     and certification are not determinable from an ingredient name),
     and anything from Bulgarian or other non-English text (unknown, not
     "suitable").
     A parenthesis group directly after an entry is a QUALIFIER of that entry
     ("Milk (plant-based)", "Milk (coconut)"): it stays attached to it. The
     bare parent counts as its own identity only when the qualifier is
     recognised as identity-PRESERVING -- a quantity that is ONLY a number,
     unit or fat/protein word ("milk (3.5% fat)", not "milk (100% plant-based)"),
     a precautionary statement ("wheat flour (may contain traces of milk)"),
     an additive list ("wheat flour (with calcium, iron)"), an E-number
     synonym ("cochineal (E120)") or a qualifier that composes with the parent
     into a known identity OF THE SAME FAMILY ("milk (skimmed)" -> "skimmed
     milk"; "milk (sugar)" is not "milk sugar"; the consumed qualifier is not
     read again, so "soy (milk)" is soy milk, not milk). Any other qualifier --
     and several or nested ones, including a second adjacent group ("milk
     (3%) (plant-based)") -- leaves the identity uncertain: the bare parent is NOT an occurrence, and
     the entry is kept only in its joined, opaque form. That is a closed list
     of identity-preserving forms, not a list of plant words; what it does not
     recognise is unknown. The group's own entries are still read, so a
     genuine compound-ingredient sublist ("chocolate (sugar, whole milk
     powder)") keeps its evidence.

Precedence when combining sources (`resolve_flags`):
  * an explicit `False` is authoritative (never overwritten);
  * derived incompatibility fills flags the source left unknown;
  * an explicit `True` that positive incompatibility evidence CONTRADICTS
    (e.g. a model says `isVegan: true` for "gelatin, pork fat") is not a
    supported suitability claim, so it becomes unknown (`None`) -- two
    conflicting claims support neither; a conflict is never resolved in
    favour of the positive claim;
  * "not vegetarian" implies "not vegan": an explicit `is_vegan=True`
    beside `is_vegetarian=False` is likewise unknown;
  * an unknown never becomes True.

Allergens (`detect_allergens_text`): a name is listed only when the same
entry-identity rules found it; "" means UNKNOWN / none detected, never
an allergen-free guarantee (only soy and milk are looked for at all).
"""
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.enums import TRUSTED_INGREDIENT_SOURCES, IngredientVerificationStatus

FLAG_NAMES: tuple[str, ...] = (
    "is_gluten_free",
    "is_lactose_free",
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
)

# Ingredient-entry identities (matched with `fullmatch` against ONE
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
_QTY = "\ue000"  # private marker left by `_prepare` where a non-zero percentage was removed
_DECORATION_RE = re.compile(r"[*\u2020\u2021\"\u201c\u201d`\ue000]")
# A parenthesis group attached to an entry ("Milk (plant-based)") is a QUALIFIER of that entry. The entry keeps its
# own identity only when the group is positively recognised as identity-preserving: a quantity ("3.5% fat", "250 ml"),
# a precautionary statement ("may contain traces of ..."), an ADDITIVE list ("with calcium, iron") or a qualifier that
# composes with the entry into a known identity ("milk (skimmed)" -> "skimmed milk"). Anything else -- "(plant-based)",
# "(coconut)", "(dairy-free)", several or nested qualifiers -- leaves the identity uncertain, so the bare parent is NOT an
# occurrence of that ingredient. Deliberately a closed list of identity-PRESERVING forms, never a list of plant words:
# whatever it does not recognise is unknown. The group's own entries are still read (a genuine sublist such as
# "Chocolate (sugar, milk powder)" keeps its evidence).
_QUANTITY_CHUNK_RE = re.compile(
    r"(?:(?:min|max|approx|ca|about|at least|up to)\s+)*[<>\u2264\u2265~]?\s*\d[\d.,]*"
    r"(?:\s*(?:mg|\u00b5g|ug|g|kg|ml|cl|dl|l|ppm|ppb|kcal|kj|fat|proteins?|solids|dry matter|by weight|total))*"
)
# "Cochineal (E120)": an E-number after a name is a synonym of it, not a qualifier of its identity.
_E_NUMBER_CHUNK_RE = re.compile(r"e\s?\d{3,4}[a-z]?")
_ADDITIVE_INTRO_RE = re.compile(
    r"^(?:with|including|incl|plus|added|containing|fortified\s+with|enriched\s+with)\b(?!\s+(?:no|non|none|not|neither)\b)"
)
_PRECAUTION_RE = re.compile(
    r"\b(?:may|might|could)\s+(?:also\s+)?contain\b|\btraces?\s+of\b|\bcross\s+contact\b"
    r"|\b(?:facility|factory|premises|equipment|bakery|production\s+line)\b"
)
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
    return " 0 " if _ZERO_RE.fullmatch(match.group(1)) else f" {_QTY} "


def _prepare(raw_text: str) -> str:
    text = raw_text[:_MAX_TEXT_CHARS].lower().replace("\u2019", "'").replace("\uff05", "%").replace(_QTY, " ")
    text = _NEWLINE_RE.sub(" ", text)  # a line wrap ("gluten-<newline>free") is whitespace, never a sentence/scope boundary
    text = _PERCENT_RE.sub(_percent_replacement, text)
    text = re.sub(r"\be\.g\.?", "eg", text)  # abbreviations must not end a sentence (and its scope)
    text = re.sub(r"\bi\.e\.?", "ie", text)
    return re.sub(r"\b(?:max|min|approx|ca|etc|vs)\.", lambda m: m.group(0)[:-1], text)


class _Group:
    """A parenthesis group: its direct chunks (raw, so a quantity marker survives), the entries read from them, whether
    it contains a nested group, and the entry it qualifies (if any)."""

    __slots__ = ("direct", "children", "nested", "owner")

    def __init__(self, owner: "_Record | None") -> None:
        self.direct: list[str] = []
        self.children: list[_Record] = []
        self.nested = False
        self.owner = owner


class _Record:
    __slots__ = ("chunk", "depth", "delimiter", "negated", "ends_free", "groups", "consumed")

    def __init__(self, chunk: str, depth: int, delimiter: str, negated: bool) -> None:
        self.chunk = chunk
        self.depth = depth
        self.delimiter = delimiter
        self.negated = negated
        self.ends_free = bool(_FREE_END_RE.search(chunk))
        self.groups: list[_Group] = []  # every parenthesis group directly after the entry ("milk (3%) (coconut)")
        self.consumed = False  # read as (part of) the qualifier of an earlier entry, so not an entry of its own


def _identity_rows(entry: str) -> frozenset[int]:
    return frozenset(index for index, (pattern, _, _) in enumerate(_ENTRY_EVIDENCE) if pattern.fullmatch(entry))


def _is_quantity(raw: str) -> bool:
    """A chunk that is ONLY a quantity: a number (a removed percentage counts as one), optionally a unit or a
    fat/protein word. "3.5% fat" is; "100% plant-based" and "3% coconut" are not."""
    return bool(_QUANTITY_CHUNK_RE.fullmatch(_normalize_entry(raw.replace(_QTY, " 0 "))))


def _qualifier_entries(record: _Record, part: str, single_part: bool) -> tuple[str, ...]:
    """The entries an ingredient `part` stands for, given the qualifier group(s) attached to it: the part itself when
    its identity is preserved, the composed identity ("skimmed milk"; only within the part's own identity family, so
    "milk (sugar)" is not "milk sugar"), or only the opaque joined form ("milk (plant based)") when the qualifier
    leaves the identity uncertain."""
    groups = record.groups
    if not groups:
        return (part,)
    qualifiers: list[str] = []
    for group in groups:
        for raw in group.direct:
            chunk = _normalize_entry(raw)
            if (
                chunk
                and not _is_quantity(raw)
                and not _PRECAUTION_RE.search(chunk)
                and not _E_NUMBER_CHUNK_RE.fullmatch(chunk)
            ):
                qualifiers.append(chunk)
    nested = any(group.nested for group in groups)
    if len(groups) == 1 and qualifiers and _ADDITIVE_INTRO_RE.match(qualifiers[0]):
        return (part,)
    if not qualifiers:
        return (part,) if not nested else ()
    if len(qualifiers) == 1 and not nested and single_part:
        base = _identity_rows(part)
        for composed in (f"{qualifiers[0]} {part}", f"{part} {qualifiers[0]}"):
            if base & _identity_rows(composed):
                for group in groups:  # the qualifier is consumed by the composed identity ("soy (milk)" is not milk)
                    for child in group.children:
                        child.consumed = True
                return (composed,)
        return (f"{part} ({qualifiers[0]})",)
    return ()


def _sentence_entries(sentence: str) -> Iterator[str]:
    tokens = _TOKEN_RE.findall(sentence)
    records: list[_Record] = []
    depth = 0
    scope_depth: int | None = None
    previous = ""
    groups: list[_Group] = []  # the open parenthesis groups, innermost last
    owner: _Record | None = None  # the entry a "(" right now would qualify
    for index, token in enumerate(tokens):
        if token in ("(", "["):
            depth += 1
            group = _Group(owner)
            if owner is not None:
                owner.groups.append(group)
            if groups:
                groups[-1].nested = True
            groups.append(group)
            owner = None
            continue
        if token in (")", "]"):
            depth = max(depth - 1, 0)
            owner = groups.pop().owner if groups else None  # a second adjacent group qualifies the same entry
            if scope_depth is not None and depth < scope_depth:
                scope_depth = None
            continue
        if token in (",", ";", ":", "/", "|"):
            owner = None
            continue
        chunk = _normalize_entry(token)
        if not chunk:
            continue
        owner = None
        if groups:
            groups[-1].direct.append(token)
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
        record = _Record(chunk, depth, delimiter, negated)
        records.append(record)
        if groups:
            groups[-1].children.append(record)
        owner = record

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
        if record.negated or record.consumed:
            continue
        parts = [part for part in _AND_SPLIT_RE.split(_LEADING_FILLER_RE.sub("", record.chunk, count=1)) if part]
        for part in parts:
            yield from _qualifier_entries(record, part, len(parts) == 1)


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


def coerce_tri_state(value: Any) -> bool | None:
    """Strict JSON-boolean-or-unknown: only a real `bool` is an explicit
    value. `None`, strings ("true"/"yes"), numbers (0/1), lists and any
    other malformed input are UNKNOWN, never coerced to True or False."""
    return value if isinstance(value, bool) else None


def _is_trusted_catalog_row(ingredient: Any) -> bool:
    """True only for a catalog row whose own dietary flags are EVIDENCE:
    `VERIFIED` and sourced from a curated/regulatory source. Booleans on an
    UNVERIFIED / OCR- or Gemini-observed row (including legacy rows written
    before the flags became nullable), on a `LIMITED_DATA` row, on a row
    with unknown provenance, or on a row flagged identity-uncertain are not
    evidence, however definite they look, and must never become a
    product-level claim."""
    return (
        getattr(ingredient, "verification_status", None) == IngredientVerificationStatus.VERIFIED
        and getattr(ingredient, "source", None) in TRUSTED_INGREDIENT_SOURCES
        and getattr(ingredient, "identity_uncertain", False) is not True
    )


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


def _catalog_incompatibilities(ingredients: Iterable[Any], entries: Iterable[str] = ()) -> set[str]:
    entries = list(entries)
    found: set[str] = set()
    for ing in ingredients:
        if not _is_trusted_catalog_row(ing) or not _is_named_by_entries(ing, entries):
            continue
        # Note the per-ingredient column semantics (see
        # `app.models.ingredient.Ingredient`): `is_gluten`/`is_lactose`
        # mean "CONTAINS", the other four mean "suitable for".
        if getattr(ing, "is_gluten", None) is True:
            found.add("is_gluten_free")
        if getattr(ing, "is_lactose", None) is True:
            found.add("is_lactose_free")
        if getattr(ing, "is_vegan", None) is False:
            found.add("is_vegan")
        if getattr(ing, "is_vegetarian", None) is False:
            # Not vegetarian implies not vegan.
            found.add("is_vegetarian")
            found.add("is_vegan")
        if getattr(ing, "is_halal", None) is False:
            found.add("is_halal")
        if getattr(ing, "is_kosher", None) is False:
            found.add("is_kosher")
    return found


def derive_incompatibilities(raw_text: str | None, ingredients: Iterable[Any] = ()) -> dict[str, bool]:
    """`{flag: False}` for every flag with SUPPORTED incompatibility
    evidence -- trusted catalog rows or exact ingredient-entry identity in
    `raw_text` (see the module docstring); flags with no such evidence are
    simply absent (unknown) -- never `True`."""
    entries = list(_evidential_entries(raw_text)) if raw_text and raw_text.strip() else []
    supported = _catalog_incompatibilities(ingredients, entries) | _evidence_from_entries(entries).flags
    return {name: False for name in FLAG_NAMES if name in supported}


def resolve_flags(
    explicit: Mapping[str, Any] | None,
    raw_text: str | None,
    ingredients: Iterable[Any] = (),
) -> dict[str, bool | None]:
    """The six product-level flags as `True`/`False`/`None`.

    `explicit` -- values a source actually STATED (a provider's
    structured tags, a structured label extraction's JSON); anything not
    a real bool is unknown. `raw_text`/`ingredients` -- the evidence
    `derive_incompatibilities` may use to fill flags `explicit` left
    unknown, with `False` only.
    """
    stated = {name: coerce_tri_state((explicit or {}).get(name)) for name in FLAG_NAMES}
    derived = derive_incompatibilities(raw_text, ingredients)
    resolved: dict[str, bool | None] = {}
    for name in FLAG_NAMES:
        value = stated[name]
        if value is None:
            resolved[name] = derived.get(name)
        elif value is True and derived.get(name) is False:
            resolved[name] = None  # a positive claim contradicted by evidence is not "supported"
        else:
            resolved[name] = value
    if resolved["is_vegetarian"] is False and resolved["is_vegan"] is True:
        resolved["is_vegan"] = None  # vegan implies vegetarian; the two explicit claims conflict
    return resolved


def detect_allergens_text(raw_text: str | None) -> str:
    """Comma-separated names of the allergens `raw_text` positively
    DECLARES as an ingredient entry ("" when none was found).

    "" is UNKNOWN, not "allergen-free": only two of the 14 regulated
    allergen categories (soy, milk) are looked for, so "found nothing"
    says nothing about the other twelve -- nor about text in any language
    it does not know, nor about a plant-based "coconut milk" (which is
    neither reported as Milk nor cleared of it). It must therefore never
    be rendered as the literal string "None" (a confirmed-absence claim).
    """
    found = derive_text_evidence(raw_text).allergens
    return ", ".join(name for name in _ALLERGEN_ORDER if name in found)


# Strings a provider/model sometimes sends INSTEAD of an allergen name to
# mean "no allergens" (or nothing at all). None of them is an allergen and
# none may be stored as a confirmed-absence claim.
_NO_ALLERGEN_PHRASES = {
    "no allergens", "no allergen", "no known allergens", "allergen free", "allergen-free",
    "none declared", "none reported", "not applicable",
}
_PLACEHOLDER_ALLERGEN_NAMES = {"", "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown"}


def clean_allergen_names(items: Iterable[Any]) -> list[str]:
    """The positive allergen names in `items`: strings that are not blank,
    not a placeholder ("None", "N/A", "null", ...) and not a "no allergens"
    phrase, de-duplicated case-insensitively in first-seen order."""
    names: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        key = cleaned.strip("\"'").strip().lower()
        if key in _PLACEHOLDER_ALLERGEN_NAMES or key in _NO_ALLERGEN_PHRASES or key in seen:
            continue
        seen.add(key)
        names.append(cleaned)
    return names
