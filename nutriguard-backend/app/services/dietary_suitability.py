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

The ABSENCE of a keyword is never evidence of suitability. Earlier code
computed e.g. `is_vegan = not ("pork" in text or "gelatin" in text or
"milk" in text)` over the raw label text: an English-only substring
check that returned `True` ("suitable") for Bulgarian, mixed-language,
foreign-language, empty or truncated text simply because none of the
English words happened to appear. Adding more keywords would not fix
that -- it would only move the blind spot. This module therefore NEVER
derives `True` from text or from a matched ingredient list: a named
ingredient is not sufficient to infer suitability (lactose status,
certification, manufacturing cross-contact, source-dependent additives
all matter). `True` only ever enters through an EXPLICIT source value
(see `resolve_flags`'s `explicit` argument).

What this module CAN derive is `False`, from two independent kinds of
positive evidence, both of which are meaningful in any language mix:

  1. Curated catalog evidence: a matched ingredient whose own (already
     tri-state) flag explicitly says it contains gluten/lactose or is
     not vegan/vegetarian/halal/kosher. Ingredient flags of `None`
     (every OCR-only/synthetic ingredient) contribute nothing.
  2. The legacy English keyword hits (`_INCOMPATIBILITY_KEYWORDS`,
     unchanged from the previous heuristic -- NOT extended), now treated
     purely as an incompatibility signal, with an explicit-negation
     guard so "gluten-free" / "milk free" / "no alcohol" are not read as
     the presence of the thing being negated. A keyword hit is a
     heuristic (it can over-report, e.g. a plant "coconut milk"), which
     is exactly why it can only ever produce the conservative
     direction; it is documented in README section 6.

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
"""
import re
from collections.abc import Iterable, Mapping
from typing import Any

FLAG_NAMES: tuple[str, ...] = (
    "is_gluten_free",
    "is_lactose_free",
    "is_vegan",
    "is_vegetarian",
    "is_halal",
    "is_kosher",
)

# The exact keyword sets the previous `fallback_local_analysis` used
# (kept verbatim so no incompatibility signal that existed before is
# lost) -- now only ever evidence for `False`.
_INCOMPATIBILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "is_gluten_free": ("wheat", "gluten"),
    "is_lactose_free": ("milk", "whey", "lactose"),
    "is_vegan": ("pork", "gelatin", "milk"),
    "is_vegetarian": ("pork", "gelatin", "bacon"),
    "is_halal": ("pork", "alcohol"),
    "is_kosher": ("pork",),
}

# A keyword immediately followed by "free"/"-free" ("gluten-free",
# "milk free", "alcoholfree"), or preceded by an explicit negation
# ("free from milk", "without wheat", "no alcohol", "non-alcoholic"),
# is a statement that the thing is ABSENT -- not evidence it is present.
_NEGATED_SUFFIX_RE = re.compile(r"^[\s\-]*free\b")
_NEGATED_PREFIX_RE = re.compile(r"(?:free\s+(?:from|of)|without|\bno|\bnon)[\s\-]*$")


def coerce_tri_state(value: Any) -> bool | None:
    """Strict JSON-boolean-or-unknown: only a real `bool` is an explicit
    value. `None`, strings ("true"/"yes"), numbers (0/1), lists and any
    other malformed input are UNKNOWN, never coerced to True or False."""
    return value if isinstance(value, bool) else None


def keyword_present(text: str, keyword: str) -> bool:
    """True when `keyword` occurs in the (already lower-cased) `text` at
    least once WITHOUT an explicit negation around it. Substring
    semantics are kept on purpose (`milk` also matches `buttermilk`):
    this is an incompatibility heuristic, and over-matching there is
    the conservative direction."""
    start = 0
    while True:
        index = text.find(keyword, start)
        if index == -1:
            return False
        end = index + len(keyword)
        if not _NEGATED_SUFFIX_RE.match(text[end:]) and not _NEGATED_PREFIX_RE.search(text[:index]):
            return True
        start = end


def _catalog_incompatibilities(ingredients: Iterable[Any]) -> set[str]:
    found: set[str] = set()
    for ing in ingredients:
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
    evidence in `raw_text`/`ingredients`; flags with no such evidence are
    simply absent (unknown) -- never `True`."""
    derived: dict[str, bool] = {name: False for name in _catalog_incompatibilities(ingredients)}
    lower = (raw_text or "").lower()
    if lower.strip():
        for name, keywords in _INCOMPATIBILITY_KEYWORDS.items():
            if name not in derived and any(keyword_present(lower, kw) for kw in keywords):
                derived[name] = False
    return derived


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


_ALLERGEN_KEYWORDS: tuple[tuple[str, str], ...] = (("Soy", "soy"), ("Milk", "milk"))


def detect_allergens_text(raw_text: str | None) -> str:
    """Comma-separated names of the allergens the keyword heuristic
    positively FOUND in `raw_text` ("" when none was found).

    "" is UNKNOWN, not "allergen-free": the heuristic only looks for two
    of the 14 regulated allergen categories (soy, milk), so "found
    nothing" says nothing about the other twelve -- and nothing about
    text in any language it does not know. It must therefore never be
    rendered as the literal string "None" (a confirmed-absence claim).
    """
    lower = (raw_text or "").lower()
    return ", ".join(name for name, keyword in _ALLERGEN_KEYWORDS if keyword_present(lower, keyword))


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
