"""
Issue #23 (stage 2): pure, deterministic classification of an observed
ingredient token for the candidate queue (`app.services.ingredient_candidates`).

Two layers, deliberately separate:

  * OCR JUNK (`JUNK_FLAGS`) -- text that is provably not an ingredient
    name: a system-generated placeholder sentence, or a token with no
    letter in it. Junk never becomes a catalog identity and is not
    returned as an ingredient; it is only counted in the queue.
  * ANOMALY flags -- the token still resolves exactly as before, but a
    reviewer should look: a fused section header, an allergen advisory, a
    functional-class word instead of a name, unbalanced brackets, an
    E-number that disagrees with the identity the name resolved to, or a
    resolved identity that names a specific source the token does not.

Nothing here merges, splits, translates or promotes anything. It reads
text and returns closed-vocabulary codes; it never looks at similarity.
The vocabularies are small and explicit on purpose (a wrong extra word
must be a visible one-line diff, never a learned model).
"""
from __future__ import annotations

import re
from enum import Enum

from app.services.ingredient_normalization import normalize_ingredient_name


class CandidateFlag(str, Enum):
    # --- OCR junk: never an ingredient identity ---
    # The system's own fallback/error sentence, not label content.
    PLACEHOLDER_TEXT = "PLACEHOLDER_TEXT"
    # Digits/punctuation only ("1234", "%%").
    NO_LETTERS = "NO_LETTERS"
    # --- Anomalies: identity resolution unchanged, flagged for review ---
    # A label section header fused into the token ("Ingredients: water").
    HEADER_ARTIFACT = "HEADER_ARTIFACT"
    # A functional-class prefix fused to a name ("Colorant: e150d").
    CLASS_PREFIXED = "CLASS_PREFIXED"
    # "May contain ..." trace/allergen advisory, not an ingredient.
    ALLERGEN_STATEMENT = "ALLERGEN_STATEMENT"
    # A functional-class word ("colour"), not the identity of an additive.
    GENERIC_FUNCTION_TERM = "GENERIC_FUNCTION_TERM"
    UNBALANCED_PARENTHESIS = "UNBALANCED_PARENTHESIS"
    # The catalog row this token resolved to is already identity_uncertain
    # (suspected merge/translation not reliably verified).
    IDENTITY_UNCERTAIN = "IDENTITY_UNCERTAIN"
    # The token's E-number differs from the E-number of the identity its
    # NAME resolved to.
    CONFLICTING_IDENTIFIER = "CONFLICTING_IDENTIFIER"
    # The resolved identity names a specific source (soy, palm, ...) that
    # the token does not, or the two name different sources.
    GENERIC_VS_SPECIFIC = "GENERIC_VS_SPECIFIC"
    # The name was longer than the queue bound and was truncated.
    TOO_LONG = "TOO_LONG"


JUNK_FLAGS = frozenset({CandidateFlag.PLACEHOLDER_TEXT, CandidateFlag.NO_LETTERS})

# Bounds shared with the model/repository.
NAME_MAX_LENGTH = 128
KEY_MAX_LENGTH = 128
FLAGS_MAX_LENGTH = 255

# The two sentences `food_analysis._run_label_image_pipeline` used to feed
# to its local fallback when the provider failed. They are error text,
# never label content (normalized form, whole-token or fragment match).
_PLACEHOLDER_FRAGMENTS = (
    "ingredients could not be extracted",
    "ai response was unavailable or invalid",
    "could not be extracted from the image",
)

_HEADER_RE = re.compile(
    r"^\s*(ingredients?|contains?|composition|съставки|състав|съдържа)\s*:", re.IGNORECASE
)
_ALLERGEN_RE = re.compile(
    r"^\s*(may\s+contain|contains\s+traces|traces\s+of|може\s+да\s+съдържа|съдържа\s+следи)\b",
    re.IGNORECASE,
)
# "<functional class>: <name>" -- English and Bulgarian.
_CLASS_PREFIX_RE = re.compile(
    r"^\s*(colou?rants?|colou?rs?|emulsifiers?|preservatives?|antioxidants?|sweeteners?|thickeners?|"
    r"stabili[sz]ers?|acidity\s+regulators?|flavou?rings?|flavou?rs?|raising\s+agents?|"
    r"оцветител(и)?|емулгатор(и)?|консервант(и)?|антиоксидант(и)?|подсладител(и)?|"
    r"сгъстител(и)?|стабилизатор(и)?|ароматизант(и)?)\s*:",
    re.IGNORECASE,
)
# A functional class on its own is not an identity.
_GENERIC_FUNCTION_TERMS = frozenset(
    {
        "colour", "colours", "color", "colors", "colorant", "colorants", "colouring", "coloring",
        "flavouring", "flavourings", "flavoring", "flavorings", "flavour", "flavor", "flavours", "flavors",
        "emulsifier", "emulsifiers", "preservative", "preservatives", "antioxidant", "antioxidants",
        "sweetener", "sweeteners", "thickener", "thickeners", "stabiliser", "stabilisers",
        "stabilizer", "stabilizers", "acidity regulator", "acidity regulators", "raising agent",
        "raising agents", "gelling agent", "glazing agent", "anti-caking agent", "flour treatment agent",
        "additive", "additives", "spice", "spices", "herbs", "seasoning",
        "ароматизант", "ароматизанти", "оцветител", "оцветители", "емулгатор", "емулгатори",
        "консервант", "консерванти", "антиоксидант", "антиоксиданти", "подсладител", "подсладители",
        "сгъстител", "сгъстители", "стабилизатор", "стабилизатори", "регулатор на киселинността",
        "подправки", "добавка", "добавки",
    }
)

# Source words that make an identity specific. Each group is one source;
# a token/identity "names" it when any variant appears as a whole word.
_SOURCE_WORDS: dict[str, frozenset[str]] = {
    "soy": frozenset({"soy", "soya", "soybean", "соя", "соев", "соева", "соево", "соеви"}),
    "palm": frozenset({"palm", "палмово", "палмов", "палмова", "палма"}),
    "corn": frozenset({"corn", "maize", "царевично", "царевичен", "царевица"}),
    "milk": frozenset({"milk", "мляко", "млечен", "млечна"}),
    "egg": frozenset({"egg", "eggs", "яйце", "яйца", "яйчен"}),
    "wheat": frozenset({"wheat", "пшеница", "пшеничен", "пшенично"}),
    "sunflower": frozenset({"sunflower", "слънчогледов", "слънчогледово", "слънчоглед"}),
    "rapeseed": frozenset({"rapeseed", "canola", "рапица", "рапичен"}),
    "coconut": frozenset({"coconut", "кокосов", "кокосово", "кокос"}),
}
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def candidate_key(name: str) -> str:
    """Stable, bounded dedup key: the normalized name, or for a very long
    one its prefix plus a content hash, so two distinct long names never
    share a key and one name always maps to the same key."""
    import hashlib

    normalized = normalize_ingredient_name(name)
    if len(normalized) <= KEY_MAX_LENGTH:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:28]
    return f"{normalized[: KEY_MAX_LENGTH - 29]}~{digest}"


def bounded_display_name(name: str) -> tuple[str, bool]:
    """`(name cut to NAME_MAX_LENGTH, was_truncated)`."""
    text = (name or "").strip()
    return (text[:NAME_MAX_LENGTH], len(text) > NAME_MAX_LENGTH)


def _source_groups(text: str) -> frozenset[str]:
    words = set(_WORD_RE.findall(normalize_ingredient_name(text)))
    return frozenset(group for group, variants in _SOURCE_WORDS.items() if words & variants)


def classify_token(name: str) -> frozenset[CandidateFlag]:
    """Flags decidable from the token text alone."""
    flags: set[CandidateFlag] = set()
    text = name or ""
    normalized = normalize_ingredient_name(text)

    if not any(ch.isalpha() for ch in text):
        flags.add(CandidateFlag.NO_LETTERS)
    if any(fragment in normalized for fragment in _PLACEHOLDER_FRAGMENTS):
        flags.add(CandidateFlag.PLACEHOLDER_TEXT)
    if _HEADER_RE.match(text):
        flags.add(CandidateFlag.HEADER_ARTIFACT)
    elif _CLASS_PREFIX_RE.match(text):
        flags.add(CandidateFlag.CLASS_PREFIXED)
    if _ALLERGEN_RE.match(text):
        flags.add(CandidateFlag.ALLERGEN_STATEMENT)
    if normalized in _GENERIC_FUNCTION_TERMS:
        flags.add(CandidateFlag.GENERIC_FUNCTION_TERM)
    if text.count("(") != text.count(")") or text.count("[") != text.count("]"):
        flags.add(CandidateFlag.UNBALANCED_PARENTHESIS)
    if len(text.strip()) > NAME_MAX_LENGTH:
        flags.add(CandidateFlag.TOO_LONG)
    return frozenset(flags)


def classify_resolution(
    *,
    token_name: str,
    token_e_number: str | None,
    resolved_name: str | None,
    resolved_e_number: str | None,
    resolved_identity_uncertain: bool,
) -> frozenset[CandidateFlag]:
    """Flags that need the identity the token resolved to."""
    flags: set[CandidateFlag] = set()
    if resolved_identity_uncertain:
        flags.add(CandidateFlag.IDENTITY_UNCERTAIN)
    if token_e_number and resolved_e_number and token_e_number.upper() != resolved_e_number.upper():
        flags.add(CandidateFlag.CONFLICTING_IDENTIFIER)
    if resolved_name:
        token_sources = _source_groups(token_name)
        resolved_sources = _source_groups(resolved_name)
        if resolved_sources != token_sources and (resolved_sources or token_sources):
            # e.g. token "Lecithin (E322)" resolved to "Soy Lecithin", or
            # "sunflower lecithin" resolved to "Soy Lecithin".
            flags.add(CandidateFlag.GENERIC_VS_SPECIFIC)
    return frozenset(flags)


def is_junk(flags: frozenset[CandidateFlag] | set[CandidateFlag]) -> bool:
    return bool(set(flags) & JUNK_FLAGS)


def serialize_flags(flags: frozenset[CandidateFlag] | set[CandidateFlag]) -> str:
    """Sorted, comma-joined closed-vocabulary codes (bounded by the
    vocabulary size, well under FLAGS_MAX_LENGTH)."""
    return ",".join(sorted(f.value for f in flags))


def parse_flags(raw: str | None) -> frozenset[CandidateFlag]:
    """Inverse of `serialize_flags`; unknown codes are dropped."""
    out: set[CandidateFlag] = set()
    for part in (raw or "").split(","):
        try:
            out.add(CandidateFlag(part.strip()))
        except ValueError:
            continue
    return frozenset(out)
