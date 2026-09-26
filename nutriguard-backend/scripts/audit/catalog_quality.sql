-- Run with psql -X -qAt -v ON_ERROR_STOP=1. Only aggregate data leaves the DB.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '2s';
WITH refs AS (
  SELECT p.barcode, trim(r.id) AS id
  FROM products p CROSS JOIN LATERAL
    unnest(string_to_array(p.ingredient_ids, ',')) AS r(id)
  WHERE trim(r.id) <> ''
), missing AS (
  SELECT r.* FROM refs r LEFT JOIN ingredients i ON i.id = r.id WHERE i.id IS NULL
), duplicates AS (
  -- Candidates for review only: equal names do not prove equal identities.
  SELECT lower(trim(common_name)), count(*) AS n FROM ingredients
  WHERE trim(common_name) <> '' GROUP BY lower(trim(common_name)) HAVING count(*) > 1
)
SELECT json_build_object(
  'schemaVersion', 1,
  'generatedAt', to_char(current_timestamp AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
  'metrics', json_build_object(
    'ingredients', (SELECT count(*) FROM ingredients),
    'products', (SELECT count(*) FROM products),
    'aliases', (SELECT count(*) FROM ingredient_aliases),
    'unverifiedIngredients', (SELECT count(*) FROM ingredients WHERE verification_status::text = 'UNVERIFIED'),
    'uncertainIdentities', (SELECT count(*) FROM ingredients WHERE identity_uncertain),
    'emptyNames', (SELECT count(*) FROM ingredients WHERE trim(common_name) = ''),
    'namesWithoutLetters', (SELECT count(*) FROM ingredients WHERE common_name !~ '[[:alpha:]]'),
    'headerCandidates', (SELECT count(*) FROM ingredients WHERE common_name ~* '^(ingredients|съставки|ingrediente)[[:space:]]*:'),
    'duplicateNameGroups', (SELECT count(*) FROM duplicates),
    'duplicateNameExtraRows', (SELECT coalesce(sum(n - 1), 0) FROM duplicates),
    'eNumberIngredients', (SELECT count(*) FROM ingredients WHERE coalesce(trim(e_number), '') <> ''),
    'invalidENumberFormat', (SELECT count(*) FROM ingredients WHERE coalesce(trim(e_number), '') <> '' AND e_number !~* '^E[0-9]{3,4}[a-z]?(\([ivx]+\))?$'),
    'missingDescriptions', (SELECT count(*) FROM ingredients WHERE lower(trim(coalesce(description, ''))) IN ('', 'none', 'null', 'unknown', 'n/a', '-')),
    'eNumbersMissingDescriptions', (SELECT count(*) FROM ingredients WHERE coalesce(trim(e_number), '') <> '' AND lower(trim(coalesce(description, ''))) IN ('', 'none', 'null', 'unknown', 'n/a', '-')),
    'missingReferenceIds', (SELECT count(DISTINCT id) FROM missing),
    'productsWithMissingReferences', (SELECT count(DISTINCT barcode) FROM missing),
    'unreferencedIngredients', (SELECT count(*) FROM ingredients i WHERE NOT EXISTS (SELECT 1 FROM refs r WHERE r.id = i.id)),
    'reviewedBgTranslations', (SELECT count(*) FROM ingredient_localizations WHERE language = 'bg' AND translation_status::text = 'REVIEWED')
  )
);
ROLLBACK;
