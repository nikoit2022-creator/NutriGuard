"""
Suspected-OCR-concatenation / ambiguous-segmentation detection for one
already-tokenized ingredient-list entry (see
`app.services.ocr_normalizer.normalize_and_extract_tokens`).

Task requirement: "Treat suspected OCR concatenation or ambiguous
segmentation as unresolved; translation alone must not certify
identity." This module never tries to re-segment or repair such a
token -- it only decides whether one should be trusted at all. Callers
(`app.services.ingredient_catalog.materialize_ingredients`) must skip
translation entirely for a flagged token and mark the resulting
ingredient `identity_uncertain=True` instead, so the client can offer
another label photo (see `app.schemas.ingredient.IngredientOut.identity_uncertain`).

Why capitalization is NOT used as evidence (code-review finding): an
earlier version of this module flagged any token containing both a
lowercase word and an ALL-CAPS word of 3+ letters, on the theory that a
capitalization SWITCH within one token marks an EU-style embedded
allergen emphasis (Regulation (EU) 1169/2011) merged into an adjacent
clause. That heuristic was too broad -- EU labels routinely emphasize a
SINGLE allergen word in ALL CAPS within an otherwise completely normal,
valid, single ingredient description ("MILK powder", "ZARA pudră",
"Produs din GRAU" / "product from WHEAT"). Ordinary allergen emphasis
is not, by itself, evidence of a broken/concatenated OCR token, so
capitalization alone must never trigger a flag here.

Why a literal colon is used instead: tokenization
(`ocr_normalizer.normalize_and_extract_tokens`) only splits on
`,`/`;`/`.` -- it never splits on `:`. A genuine EU-label clause-header
pattern, e.g. a functional-class header followed by its own sub-list
("agenti de crestere: enzime (SECARA)", "Contains: milk, soy"), can
therefore survive tokenization as ONE token instead of being split into
its real, separate parts. Unlike a capitalization pattern (which also
fires on entirely normal phrasing), a colon still present inside a
token is real, deterministic, structural evidence that a clause
boundary tokenization was supposed to cut on is still there -- not a
guess about typography.

Deliberately conservative -- biased toward NOT flagging, matching this
codebase's existing "fail toward less certain, not toward a wrong
positive" style (see `app.services.language_detection`'s own module
docstring). A false negative here (a genuinely ambiguous token that
slips through) just gets translated as usual, no worse than before this
module existed; a false positive would block a perfectly good
ingredient's identity resolution for no reason.
"""
import re

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# A token this long, with no sentence punctuation left to have split it
# (tokenization already ran), is more likely a mis-split multi-clause
# fragment than one real ingredient name.
_LONG_TOKEN_WORD_COUNT = 7


def _words(token: str) -> list[str]:
    return _WORD_RE.findall(token)


def _has_unsplit_colon_clause(token: str) -> bool:
    """A literal `:` still present inside `token`.

    `ocr_normalizer.normalize_and_extract_tokens` tokenizes an
    ingredient list by splitting only on `,`/`;`/`.` -- it deliberately
    never splits on `:` (see that function). That means a genuine
    EU-label clause-header pattern -- a functional-class header
    followed by its own sub-list, e.g. "agenti de crestere: enzime
    (SECARA)" or "Contains: milk, soy" -- can survive tokenization as
    ONE token instead of being split into its real, separate parts.

    This is real, structural evidence, not a guessed pattern: the colon
    is the exact character tokenization was never told to cut on, so
    its presence here means a clause boundary genuinely exists inside
    what is supposed to be a single ingredient name. Unlike bare
    capitalization (which also fires on ordinary allergen-emphasis
    phrasing -- see the module docstring), a colon has no legitimate
    role inside one ingredient's own name."""
    return ":" in token


def _has_duplicate_word(words: list[str]) -> bool:
    """A significant word (4+ letters, to skip short connector words
    like "de"/"din"/"cu"/"and"/"or") repeated within the same token --
    the "LAPTE proteina din LAPTE" ("MILK protein from MILK") shape,
    where two adjacent list clauses were merged around a shared
    allergen word."""
    seen: set[str] = set()
    for w in words:
        lowered = w.lower()
        if len(lowered) < 4:
            continue
        if lowered in seen:
            return True
        seen.add(lowered)
    return False


def detect_ambiguous_segmentation(token: str) -> str | None:
    """Returns a short, stable reason code if `token` looks like a
    merged/mis-segmented fragment rather than one reliable ingredient
    name, else `None`. Pure and deterministic -- never touches the
    network, never consults a language model.
    """
    if not token or not token.strip():
        return None
    words = _words(token)
    if not words:
        return None

    if _has_unsplit_colon_clause(token):
        return "COLON_SEPARATED_CLAUSE_MERGE"

    if _has_duplicate_word(words):
        return "DUPLICATE_TOKEN_FRAGMENT"

    if len(words) >= _LONG_TOKEN_WORD_COUNT:
        return "LONG_UNSEGMENTED_CLAUSE"

    return None
