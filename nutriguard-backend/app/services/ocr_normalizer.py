"""
Deterministic port of `com.example.service.ocr.OcrNormalizer`.

Tokenization rules, matching heuristics, and synthetic-ingredient risk
heuristics are copied verbatim from the Kotlin source (API Contract
section 7.3).
"""
import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any

from app.models.enums import RiskLevel

_BRACKET_OR_PERCENT = re.compile(r"\[.*?\]|\(.*?%\)")
_NON_WORD_EDGES = re.compile(r"^\W+|\W+$")
_E_NUMBER = re.compile(r"e[- ]?(\d{3,4}[a-z]?)", re.IGNORECASE)

# `Ingredient.id` is `String(64)` (see app/models/ingredient.py) -- every
# synthetic id generated below MUST fit inside that limit regardless of
# how long or how heavily-punctuated the OCR name is, or the insert
# fails outright for a real product's label. `_ID_PREFIX` + an
# underscore + a `_HASH_LEN`-hex-char content hash is the fixed,
# non-negotiable tail; whatever's left is the readable slug's budget.
_ID_PREFIX = "synth_"
_HASH_LEN = 12
_MAX_ID_LEN = 64
_MAX_SLUG_LEN = _MAX_ID_LEN - len(_ID_PREFIX) - _HASH_LEN - 1  # -1 for the slug/hash separator


@dataclass(frozen=True)
class NormalizedIngredientResult:
    matched_ingredients: list[Any]
    unknown_ingredients: list[str]
    raw_tokens: list[str]


def normalize_and_extract_tokens(raw_text: str) -> list[str]:
    cleaned = _BRACKET_OR_PERCENT.sub("", raw_text)
    cleaned = cleaned.replace("\n", " ")
    cleaned = re.sub(re.escape("Ingredients:"), "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(re.escape("CONTAINS:"), "", cleaned, flags=re.IGNORECASE)

    tokens = re.split(r"[,;.]", cleaned)
    result = []
    for token in tokens:
        t = token.strip()
        t = _NON_WORD_EDGES.sub("", t)
        if len(t) > 1:
            result.append(t)
    return result


def match_against_database(tokens: list[str], db_ingredients: list[Any]) -> NormalizedIngredientResult:
    matched: list[Any] = []
    unknown: list[str] = []

    for token in tokens:
        lower_token = token.lower()
        found = None

        e_match = _E_NUMBER.search(lower_token)
        if e_match:
            formatted_e = "E" + e_match.group(1).upper()
            found = next(
                (ing for ing in db_ingredients if (ing.e_number or "").upper() == formatted_e.upper()),
                None,
            )

        if found is None:
            for ing in db_ingredients:
                common = ing.common_name.lower()
                sci = (ing.scientific_name or "").lower()
                if (
                    (common and (lower_token in common or common in lower_token))
                    or (sci and lower_token in sci)
                ):
                    found = ing
                    break

        if found is not None:
            if found not in matched:
                matched.append(found)
        else:
            if token and token not in unknown:
                unknown.append(token)

    return NormalizedIngredientResult(matched_ingredients=matched, unknown_ingredients=unknown, raw_tokens=tokens)


@dataclass
class SyntheticIngredient:
    """Plain in-memory stand-in for an IngredientEntity that has no
    scientific-database match. It is built ONLY from what the OCR text
    itself genuinely provides (the raw name, and an E-number when the
    text literally contains one) -- OCR is provenance, not scientific
    evidence, so every field that would require verified scientific/
    regulatory data (description, purpose, health concerns, evidence
    level, EFSA/FDA status, ADI, countries, references, risk level) is
    left honestly empty/neutral rather than filled with a fabricated
    generic placeholder. See `create_synthetic_ingredient`."""

    id: str
    common_name: str
    scientific_name: str
    e_number: str | None
    category: str
    description: str
    purpose_in_food: str
    health_concerns: str
    evidence_level: str
    countries_restricted_or_banned: str
    efsa_status: str
    fda_status: str
    who_iarc_classification: str | None
    acceptable_daily_intake: str
    side_effects: str
    allergens: str
    references: str
    risk_level: RiskLevel
    # Always False here: no scientific-database match means no real
    # assessment was ever made. `risk_level` above is a safe, neutral
    # placeholder (SAFE) -- never a keyword guess -- and callers must
    # gate on this flag rather than trusting it directly (see
    # `IngredientOut.risk_assessment_available` and
    # `food_analysis._score_and_warnings`, which excludes any
    # ingredient with this flag False from the Health Score).
    risk_assessment_available: bool = False
    # `None` -- not True, not False -- for every one of these six: OCR
    # text alone never establishes whether an ingredient is gluten-/
    # lactose-free, vegan, vegetarian, halal, or kosher. `False` would
    # silently assert "confirmed free of gluten/lactose" (a dangerous
    # false negative for someone relying on it) and `True` would
    # silently assert "confirmed vegan/vegetarian/halal/kosher" (a
    # fabricated certification with zero evidence behind it) -- see task
    # requirement 2 ("genuinely minimal and non-fabricated"). `None`
    # ("unknown") is the only honest value here, which is why these
    # columns are nullable (see app/models/ingredient.py).
    is_gluten: bool | None = None
    is_lactose: bool | None = None
    is_vegan: bool | None = None
    is_vegetarian: bool | None = None
    is_halal: bool | None = None
    is_kosher: bool | None = None
    bad_for_diabetes: bool = False
    bad_for_hypertension: bool = False
    bad_for_kidney_disease: bool = False
    bad_for_gout: bool = False
    bad_for_pregnancy: bool = False
    bad_for_children: bool = False
    bad_for_high_cholesterol: bool = False


def _synthetic_id(name: str) -> str:
    """Deterministic, collision-resistant, length-bounded id for an
    OCR-observed ingredient with no scientific-database match.

    `Ingredient.id` is `String(64)` (see `app/models/ingredient.py`) --
    this MUST never exceed 64 characters, no matter how long or
    punctuation-heavy the raw OCR name is (task: "Bound every generated
    synthetic ingredient ID to the database String(64) limit").

    Two parts, both bounded:
      - a short, readable slug (truncated ASCII letters/digits/
        underscores from the name) -- purely for human debuggability
        (log lines, DB browsing), NOT what guarantees uniqueness;
      - a fixed-length content hash of the full, untruncated,
        case-folded name -- THIS is what actually guarantees two
        distinct names never collide: two long names sharing the same
        first `_MAX_SLUG_LEN` characters, two names that differ only
        after truncation, or two names that are entirely non-ASCII
        (pure Cyrillic/Bulgarian text, where the slug is empty) all
        still get different ids, because the hash covers the whole
        name, not the truncated/possibly-empty slug.

    Deterministic (same name -> same id, so a later scan of the same
    OCR text reuses the same row -- see `ingredient_catalog`) and stable
    across process restarts (no randomness, no dict/set iteration
    order).
    """
    raw_slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))
    raw_slug = re.sub(r"_+", "_", raw_slug).strip("_")
    slug = raw_slug[:_MAX_SLUG_LEN].strip("_")
    content_hash = hashlib.sha1(name.strip().lower().encode("utf-8")).hexdigest()[:_HASH_LEN]
    return f"{_ID_PREFIX}{slug}_{content_hash}" if slug else f"{_ID_PREFIX}{content_hash}"


def create_synthetic_ingredient(name: str) -> SyntheticIngredient:
    """Build a placeholder ingredient for an OCR token that has no
    scientific-database match.

    Deliberately honest, not "helpful": OCR only ever tells us the raw
    label text (and, when literally present, an E-number) -- it is
    provenance, not scientific evidence. Every field that would require
    real curated/verified data (description, purpose, health concerns,
    evidence level, EFSA/FDA status, ADI, countries, references, and
    the risk assessment itself) is left honestly empty/neutral rather
    than filled with a fabricated generic string. `risk_level` is
    always the safe, neutral default (never inferred from a keyword in
    the OCR name) and `risk_assessment_available=False` tells callers
    (Health Score, API clients) that it is not a real assessment.
    """
    lower = name.lower()
    e_match = _E_NUMBER.search(lower)
    formatted_e = ("E" + e_match.group(1).upper()) if e_match else None

    return SyntheticIngredient(
        id=_synthetic_id(name),
        common_name=name[:1].upper() + name[1:] if name else name,
        scientific_name=formatted_e or "",
        e_number=formatted_e,
        category=f"Food Additive ({formatted_e})" if formatted_e else "Ingredient",
        description="",
        purpose_in_food="",
        health_concerns="",
        evidence_level="",
        countries_restricted_or_banned="",
        efsa_status="",
        fda_status="",
        who_iarc_classification=None,
        acceptable_daily_intake="",
        side_effects="",
        # A positive match is real evidence straight from the label text
        # itself, so it's kept -- but the absence of a match is NOT
        # proof of "no allergens" (the OCR token is one ingredient name,
        # not the full label, and this keyword list is tiny), so the
        # negative case is left "" (unknown/not stated), never the
        # literal string "None" masquerading as a verified clean bill of
        # health (task requirement 2).
        allergens=(
            "Potential Allergen"
            if any(kw in lower for kw in ("milk", "whey", "soy", "wheat", "peanut"))
            else ""
        ),
        references="",
        risk_level=RiskLevel.SAFE,
        risk_assessment_available=False,
        # `bad_for_*` used to be guessed from a handful of keywords in
        # the raw OCR name (e.g. "sugar" -> bad_for_diabetes=True) --
        # exactly the kind of fabricated medical inference task
        # requirement 2 forbids: a bare ingredient-name substring is not
        # a clinical assessment, and a wrongly-guessed True here drives
        # a real HIGH-severity personalized warning (see
        # `app.services.warning_engine`) for a condition that was never
        # actually evaluated. Left at the dataclass's own safe default
        # (False -- "not flagged", not "confirmed safe") for every
        # unverified OCR observation; a real value is only ever set by a
        # genuine regulatory/curated assessment (see
        # `app.services.ingredient_catalog._build_minimal_row`).
    )


def reconstruct_synthetic_ingredient(ingredient_id: str, raw_text: str) -> SyntheticIngredient:
    """Recover a synthetic ingredient's human label from persisted OCR text.

    Products historically stored only synthetic IDs. Rebuilding directly
    from that ID exposed implementation strings such as ``Synth_5ecbec8146``
    to users and permanently lost Cyrillic names. Recreate each raw token and
    match its deterministic ID first; use a readable legacy fallback only
    when the original token is genuinely unavailable.
    """
    for token in normalize_and_extract_tokens(raw_text or ""):
        candidate = create_synthetic_ingredient(token)
        if candidate.id == ingredient_id:
            return candidate

    # Current id shape (see `_synthetic_id`) is "<slug>_<hash>" or, when
    # the name had no ASCII-alphanumeric characters at all, just
    # "<hash>" -- strip the trailing content-hash segment so it's never
    # shown to a user as part of a "readable" reconstructed name. Also
    # tolerates the two OLDER id shapes this function already handled
    # before ids were bounded/hashed (a bare slug with no hash suffix,
    # and the previous 10-hex-char non-Latin-name fallback), for any id
    # persisted before that fix.
    body = ingredient_id.removeprefix(_ID_PREFIX)
    hash_suffix = body[-_HASH_LEN:]
    if len(body) > _HASH_LEN and body[-(_HASH_LEN + 1)] == "_" and re.fullmatch(r"[0-9a-f]+", hash_suffix):
        slug = body[: -(_HASH_LEN + 1)]
    elif len(body) in (_HASH_LEN, 10) and re.fullmatch(r"[0-9a-f]+", body):
        slug = ""
    else:
        slug = body

    if slug:
        readable = re.sub(r"_+", " ", slug).strip()
        if readable:
            return replace(create_synthetic_ingredient(readable), id=ingredient_id)

    return replace(create_synthetic_ingredient("Ingredient detected on label"), id=ingredient_id)
