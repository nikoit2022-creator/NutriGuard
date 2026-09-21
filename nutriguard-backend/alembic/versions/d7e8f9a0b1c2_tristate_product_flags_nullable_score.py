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
  * `false` was written by (a) a keyword HIT (evidence of incompatibility),
    (b) a provider/label-extraction default meaning only "not stated"
    (unknown), or (c) an explicit provider/model `false`.
`products.source` distinguishes (a) from (b) for `true`, and the stored
ingredient text lets us re-check a keyword hit for `false`. So:
  * `true` is KEPT only for rows sourced from a barcode provider
    ('open_food_facts', 'gs1_digital_link', 'upcitemdb'); for every other
    source ('local', 'label_scan', 'label_scan_translated', anything
    unrecognised) it is reset to NULL -- an unsupported positive claim is
    never preserved.
  * `false` is KEPT only when the row's stored `raw_ingredient_text` still
    contains one of the legacy incompatibility keywords for THAT flag
    (the same English sets `app.services.dietary_suitability` uses; a
    frozen SQL copy so this migration never imports application code).
    Otherwise it is reset to NULL: it was a default/unknown, and would
    now be misread as a supported incompatibility. Limitation: this check
    does not replicate the runtime negation guard ("gluten-free"), so a
    legacy `false` whose only hit is a negated phrase is conservatively
    preserved rather than destroyed.
  * `health_score` is reset to NULL for every row that is not
    `is_verified` (its value there was only ever a placeholder 0); a
    verified row's score, including a genuine 0, is untouched.
  * Trustworthy data is not indiscriminately destroyed: provider `true`s,
    keyword-supported `false`s and every verified score survive. A
    rediscovery/label re-scan repopulates the rest with the new,
    evidence-gated logic.

DOWNGRADE is LOSSY by necessity (documented): the pre-migration schema is
NOT NULL, so NULL flags are backfilled to `false` (the least-asserting
boolean, and exactly the value the previous provider path used for
"unknown") and a NULL score is backfilled to 0 (its previous placeholder).
The unknown/known distinction is lost for those rows, and the allergen
"None" placeholder is NOT restored (an empty string is a valid pre-migration
value). Consequently upgrade -> downgrade -> upgrade is not an identity on
data written after the first upgrade: re-running the policy above resets
non-provider `true`s again. Take a backup before running either direction
against real data.
"""

from alembic import op
import sqlalchemy as sa


revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None

_PROVIDER_SOURCES = ("open_food_facts", "gs1_digital_link", "upcitemdb")

# Frozen copy of `app.services.dietary_suitability._INCOMPATIBILITY_KEYWORDS`
# (== the previous `fallback_local_analysis` keyword sets).
_INCOMPATIBILITY_KEYWORDS = {
    "is_gluten_free": ("wheat", "gluten"),
    "is_lactose_free": ("milk", "whey", "lactose"),
    "is_vegan": ("pork", "gelatin", "milk"),
    "is_vegetarian": ("pork", "gelatin", "bacon"),
    "is_halal": ("pork", "alcohol"),
    "is_kosher": ("pork",),
}

# Frozen copy of `app.services.barcode_text_safety._PLACEHOLDER_VALUES`.
_PLACEHOLDER_ALLERGEN_VALUES = ("", "null", "none", "n/a", "na", "nil", "undefined", "-", "unknown")


def _sql_list(values) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def data_policy_statements() -> list[str]:
    """The legacy-data policy (see module docstring) as plain, portable SQL,
    in execution order. Exposed as a function so the default test suite can
    execute exactly these statements against representative legacy rows."""
    statements = ["UPDATE products SET health_score = NULL WHERE is_verified = false"]
    providers = _sql_list(_PROVIDER_SOURCES)
    for column, keywords in _INCOMPATIBILITY_KEYWORDS.items():
        statements.append(
            f"UPDATE products SET {column} = NULL WHERE {column} = true AND source NOT IN ({providers})"
        )
        hit = " OR ".join(f"lower(raw_ingredient_text) LIKE '%{kw}%'" for kw in keywords)
        statements.append(f"UPDATE products SET {column} = NULL WHERE {column} = false AND NOT ({hit})")
    statements.append(
        "UPDATE products SET allergens_detected = '' "
        f"WHERE lower(trim(allergens_detected)) IN ({_sql_list(_PLACEHOLDER_ALLERGEN_VALUES)})"
    )
    return statements


def downgrade_backfill_statements() -> list[str]:
    """Backfills that must run BEFORE the NOT NULL constraints are restored."""
    statements = [f"UPDATE products SET {column} = false WHERE {column} IS NULL" for column in _INCOMPATIBILITY_KEYWORDS]
    statements.append("UPDATE products SET health_score = 0 WHERE health_score IS NULL")
    return statements


def upgrade() -> None:
    op.alter_column("products", "health_score", existing_type=sa.Integer(), nullable=True)
    for column in _INCOMPATIBILITY_KEYWORDS:
        op.alter_column("products", column, existing_type=sa.Boolean(), nullable=True)

    for statement in data_policy_statements():
        op.execute(statement)


def downgrade() -> None:
    for statement in downgrade_backfill_statements():
        op.execute(statement)
    for column in _INCOMPATIBILITY_KEYWORDS:
        op.alter_column("products", column, existing_type=sa.Boolean(), nullable=False)
    op.alter_column("products", "health_score", existing_type=sa.Integer(), nullable=False)
    # allergens_detected: intentionally NOT restored to "None" (lossy, documented).
