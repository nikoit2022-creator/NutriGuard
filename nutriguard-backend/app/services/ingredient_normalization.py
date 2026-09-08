"""
Pure text normalization for ingredient canonical-identity matching (see
`app.services.ingredient_catalog`). One normalized form per distinct
identity: case, diacritics, and incidental punctuation/whitespace must
never make two mentions of the same ingredient look different, and
must never make two DIFFERENT ingredients collide either (this is
deliberately conservative -- no stemming, no fuzzy/typo correction).
"""
import re
import unicodedata

_WHITESPACE_RE = re.compile(r"\s+")
# Kept intentionally narrow: strips trailing punctuation noise a label/
# OCR pass commonly leaves ("Citric Acid.", "Sugar,") without touching
# internal characters that carry real meaning (a hyphen in
# "L-alpha-...", digits in an E-number). Deliberately excludes
# brackets/parens: a curated name like "High Fructose Corn Syrup
# (HFCS)" would otherwise strip only the trailing ")" (there being
# nothing after it) while leaving the matching "(" untouched, producing
# a lopsided, inconsistent normalized form -- an ingredient-list token
# containing meaningful bracketed content is already fully bracket-free
# by the time it reaches this function (`ocr_normalizer`'s own
# tokenization strips `[...]`/`(...%)` before matching ever begins), so
# there is no real OCR noise case this would otherwise catch.
_EDGE_PUNCTUATION_RE = re.compile(r"^[\s.,;:\"'-]+|[\s.,;:\"'-]+$")


def normalize_ingredient_name(text: str) -> str:
    """Canonical, comparable form of an ingredient name/alias: Unicode-
    normalized (NFKC), case-folded, edge punctuation stripped, internal
    whitespace collapsed to single spaces. Works uniformly for Latin
    (English) and Cyrillic (Bulgarian) text -- `casefold()` handles both
    scripts correctly, and neither uses combining diacritics in normal
    ingredient-list usage, so no separate diacritic-stripping pass is
    needed (unlike e.g. French/Spanish text, which this project's
    labels are not expected to contain -- see README "Language
    policy").
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKC", text)
    normalized = _EDGE_PUNCTUATION_RE.sub("", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized.strip())
    return normalized.casefold()
