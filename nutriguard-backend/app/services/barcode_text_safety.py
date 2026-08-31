"""
Minimal, self-contained text-safety helpers for barcode-discovery
output: placeholder scrubbing, plus a Latin/Cyrillic **script** safety
net (rejects Greek/Arabic/CJK/Thai/Hebrew/... text a client should never
see untranslated).

IMPORTANT — this is NOT the English/Bulgarian *language* policy, and
must never be treated as one: a French, German, or Albanian name is
Latin script too, so `is_safe_script`/`to_safe_display_text` cannot
reject it. The real language enforcement — "only accept an explicit
`_en`/`_bg` field, or a language-neutral field when the source's own
declared language is English/Bulgarian" — lives at the data source
(see `app/integrations/barcode_providers/open_food_facts.py`'s
`_language_gated_field`), which is the only place that actually knows
what language a value is in. This module is purely a defensive backstop
for providers with no language signal at all (UPCitemdb) and for
catching placeholder junk/genuinely-wrong scripts everywhere else.

NOTE: a more complete version of this exact idea (translation of a small
German/French/Albanian glossary, Bulgarian search-alias matching for
ingredient lookup, etc.) exists as `app/services/text_localization.py`
on the separate, not-yet-merged `fix/backend-bilingual-analysis` branch.
This module intentionally does NOT depend on that branch — `main` does
not have it, and this feature must stand on its own on `main`. If/when
that work merges, this module's two responsibilities (placeholder
scrubbing, script-safety fallback) should be consolidated into it rather
than kept as two parallel implementations — this docstring is the
pointer for that future cleanup.
"""
import re

_PLACEHOLDER_VALUES = {"", "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown"}

# Printable ASCII, Latin-1 Supplement, Latin Extended-A/B (covers German
# umlauts, French accents, etc.), Cyrillic (Bulgarian), and whitespace —
# the same two supported display scripts/languages as the rest of the
# app's API contract (English + Bulgarian). Anything else (Greek,
# Arabic, CJK, Thai, Hebrew, ...) is never returned as a display name.
_LATIN_EXTENDED_END = 0x024F
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


def is_safe_script(text: str) -> bool:
    return all(_is_safe_codepoint(ch) for ch in text)


def is_placeholder(value: str | None) -> bool:
    """True for None/blank/a literal 'null'/'None'-style placeholder a
    provider sometimes sends instead of either a real value or an
    actual JSON null."""
    if value is None:
        return True
    return value.strip().strip('"').strip("'").lower() in _PLACEHOLDER_VALUES


def clean_optional(value: str | None) -> str | None:
    """None for placeholder junk, else the trimmed value."""
    if is_placeholder(value):
        return None
    return value.strip()


def clean_required(value: str | None, fallback: str) -> str:
    """`fallback` for placeholder junk, else the trimmed value. Does NOT
    check script safety — safe for a field like `brand` that must be
    preserved verbatim, never translated or rejected for its script."""
    cleaned = clean_optional(value)
    return cleaned if cleaned else fallback


_WORD_RE = re.compile(r"\S+")


def to_safe_display_text(value: str | None, fallback: str) -> str:
    """
    Text safe to show an English- or Bulgarian-reading user:
      - placeholder junk -> `fallback`
      - a script that is neither Latin nor Cyrillic -> `fallback`
        (this module does not translate; only `text_localization.py` on
        the bilingual branch attempts that — see module docstring)
      - anything else (English, Bulgarian, or another Latin-script
        language) -> returned unchanged

    Use for a discovered product's display name/category — never for
    `brand`, which must never be altered based on its script (use
    `clean_required` instead).
    """
    cleaned = clean_optional(value)
    if not cleaned:
        return fallback
    if not is_safe_script(cleaned):
        return fallback
    return cleaned
