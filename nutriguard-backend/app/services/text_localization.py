"""
Bilingual output guard for AI-derived, user-facing text.

Every product/ingredient name the API returns is rendered directly by
the Android client, so it must be English or Bulgarian (product
requirement, see README "Deviations" entry for this module). The
primary mechanism for that is the Gemini prompts themselves
(`app/integrations/gemini.py` instructs the model to answer only in
English/Bulgarian and to translate other languages). This module is the
deterministic BACKEND SAFETY NET for when a prompt is not followed --
it is not a translator and does not attempt to be one. It stops three
concrete failure modes from reaching the API response:

  1. Literal placeholder junk ("null", "None", "N/A", "") standing in
     for a real value instead of JSON `null` or a real string.
  2. Ingredient/product text written in a script that is neither Latin
     nor Cyrillic (Greek, Arabic, CJK, Thai, Hebrew, ...). Such text can
     never be safely shown to an English/Bulgarian-only client, so it is
     replaced with a safe English fallback rather than ever leaking
     through untranslated.
  3. A small, explicitly non-exhaustive glossary of common European
     food-label words -- covering German, French and Albanian, the
     languages named in the product requirements -- so the most common
     "other language" cases are actually translated to English rather
     than merely rejected.

A Latin-script word this module has no glossary entry for is passed
through unchanged: full offline machine translation of arbitrary text
is out of scope for a deterministic backend layer. For that general
case, correctness depends on the Gemini prompt being followed; this
module only guarantees the three failure modes above never reach the
client.
"""
import hashlib
import re

_PLACEHOLDER_VALUES = {"", "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown"}

# Unicode code points considered safe to display as-is to an
# English/Bulgarian reader: printable ASCII, Latin-1 Supplement, and
# Latin Extended-A/B (covers German umlauts, French accents, Albanian
# e-with-diaeresis/c-with-cedilla etc.), plus Cyrillic (Bulgarian) and
# whitespace. Anything outside these ranges (Greek, Arabic, CJK, Thai,
# Hebrew, ...) is never returned as-is -- see `to_bilingual_display_text`.
_LATIN_EXTENDED_END = 0x024F  # end of Latin Extended-B
_CYRILLIC_START = 0x0400
_CYRILLIC_END = 0x04FF


def _is_safe_codepoint(ch: str) -> bool:
    if ch.isspace():
        return True
    code = ord(ch)
    if 0x20 <= code <= _LATIN_EXTENDED_END:
        return True
    if _CYRILLIC_START <= code <= _CYRILLIC_END:
        return True
    return False


def _is_safe_script(text: str) -> bool:
    return all(_is_safe_codepoint(ch) for ch in text)


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Non-exhaustive glossary of common food-label words in languages other
# than English/Bulgarian, mapped to canonical English. Extend as more
# real-world labels surface additional terms; an unrecognised
# Latin-script word is passed through unchanged rather than guessed at.
_FOREIGN_TERM_GLOSSARY: dict[str, str] = {
    # German
    "zucker": "Sugar", "salz": "Salt", "wasser": "Water", "milch": "Milk",
    "weizen": "Wheat", "ei": "Egg", "eier": "Eggs", "butter": "Butter",
    "sonnenblumenoel": "Sunflower Oil", "sonnenblumenöl": "Sunflower Oil",
    "produkt": "Product", "zutaten": "Ingredients", "hefe": "Yeast",
    # French
    "sucre": "Sugar", "sel": "Salt", "eau": "Water", "lait": "Milk",
    "ble": "Wheat", "blé": "Wheat", "oeuf": "Egg", "œuf": "Egg",
    "oeufs": "Eggs", "œufs": "Eggs", "beurre": "Butter", "huile": "Oil",
    "produit": "Product", "ingredients": "Ingredients",
    "ingrédients": "Ingredients", "levure": "Yeast",
    # Albanian
    "sheqer": "Sugar", "kripe": "Salt", "kripë": "Salt", "uje": "Water",
    "ujë": "Water", "qumesht": "Milk", "qumeshë": "Milk",
    "qumësht": "Milk", "gruri": "Wheat", "grure": "Wheat",
    "grurë": "Wheat", "veze": "Egg", "vezë": "Egg", "vezet": "Eggs",
    "vezët": "Eggs", "gjalpe": "Butter", "gjalpë": "Butter",
    "vaj": "Oil", "perberesit": "Ingredients", "përbëresit": "Ingredients",
    "maja": "Yeast",
}

# Bulgarian ingredient/product words -> the canonical English term used
# by the seeded scientific database, so a Bulgarian-language label can
# still be matched against it. Kept separate from _FOREIGN_TERM_GLOSSARY
# because Bulgarian text is never replaced for *display* -- it is only
# used as an extra search key (see `search_aliases_for`).
BULGARIAN_SEARCH_ALIASES: dict[str, str] = {
    "захар": "Sugar",
    "сол": "Salt",
    "вода": "Water",
    "мляко": "Milk",
    "пшеница": "Wheat",
    "яйце": "Egg",
    "яйца": "Eggs",
    "масло": "Butter",
    "мая": "Yeast",
    "аспартам": "Aspartame",
    "бензоат": "Benzoate",
    "натриев бензоат": "Sodium Benzoate",
    "натриев нитрит": "Sodium Nitrite",
    "нитрит": "Nitrite",
}


def is_placeholder(value: str | None) -> bool:
    """True if `value` is None/blank/a literal 'null'/'None'-style
    placeholder rather than a real value."""
    if value is None:
        return True
    return value.strip().strip('"').strip("'").lower() in _PLACEHOLDER_VALUES


def clean_optional(value: str | None) -> str | None:
    """None for placeholder junk, else the trimmed value. Use for
    nullable API fields -- never let a literal "null"/"None" string
    stand in for a real JSON null."""
    if is_placeholder(value):
        return None
    return value.strip()


def clean_required(value: str | None, fallback: str) -> str:
    """`fallback` for placeholder junk, else the trimmed value. Use for
    API fields that must never be null/empty. Does NOT translate --
    safe for fields like brand that must be preserved verbatim."""
    cleaned = clean_optional(value)
    return cleaned if cleaned else fallback


def _translate_known_terms(text: str) -> str:
    def _replace(match: re.Match) -> str:
        word = match.group(0)
        return _FOREIGN_TERM_GLOSSARY.get(word.lower(), word)

    return _WORD_RE.sub(_replace, text)


def to_bilingual_display_text(value: str | None, fallback: str) -> str:
    """
    Text safe to show an English- or Bulgarian-reading Android user:

      - placeholder junk               -> `fallback`
      - known German/French/Albanian terms -> translated to English
      - unsupported (non-Latin, non-Cyrillic) script -> `fallback`
        (cannot be translated deterministically; must never leak as-is)
      - anything else (English, Bulgarian, or an unrecognised
        Latin-script word) -> returned unchanged

    Best-effort safety net, not a full translator -- see module
    docstring. Use for product/ingredient *names*, never for `brand`
    (brand names are never translated -- use `clean_required` instead).
    """
    cleaned = clean_optional(value)
    if not cleaned:
        return fallback

    translated = _translate_known_terms(cleaned)

    if not _is_safe_script(translated):
        return fallback

    return translated


def search_aliases_for(token: str) -> list[str]:
    """Extra English search keys to try when matching `token` against
    the (English) scientific database -- e.g. a Bulgarian ingredient
    word resolves to its English canonical name here, without altering
    the token's own display text anywhere else."""
    lower = token.strip().lower()
    if not lower:
        return []

    aliases = []
    if lower in BULGARIAN_SEARCH_ALIASES:
        aliases.append(BULGARIAN_SEARCH_ALIASES[lower])

    translated = _translate_known_terms(token)
    if translated.lower() != lower:
        aliases.append(translated)

    return aliases


def safe_slug(name: str, fallback_prefix: str | None) -> str:
    """ASCII id-slug for `name` (e.g. for a synthetic ingredient id).
    Falls back to `fallback_prefix` (e.g. a formatted E-number) or a
    short content hash when the name has no ASCII-alphanumeric
    characters at all (a non-Latin script name would otherwise collapse
    to an empty/colliding slug)."""
    ascii_slug = re.sub(r"[^a-z0-9_]", "", name.lower().replace(" ", "_"))
    if ascii_slug:
        return ascii_slug
    if fallback_prefix:
        return re.sub(r"[^a-z0-9_]", "", fallback_prefix.lower())
    return hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
