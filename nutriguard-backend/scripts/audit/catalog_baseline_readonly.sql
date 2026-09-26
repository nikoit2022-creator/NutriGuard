-- Issue #23, stage 1: read-only catalog baseline audit.
--
-- Aggregate counts and quality flags for the ingredient catalog. SELECT
-- only, inside a READ ONLY transaction: it cannot write. It reads only
-- `ingredients`, `ingredient_aliases`, `ingredient_localizations` and
-- the `ingredient_ids` column of `products` (ids, not user data). It
-- never touches users, devices, tokens, scan history, raw OCR text or
-- logs, and it prints no per-user or per-scan values.
--
-- Run (credentials stay in the container environment, never in the
-- command line):
--   docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on' <db-container> \
--     sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--     < scripts/audit/catalog_baseline_readonly.sql
--
-- Output is one labelled row per line: `section | key... | value`.
BEGIN READ ONLY;
\pset tuples_only on
\pset format unaligned
\pset fieldsep ' | '

-- 1. Table sizes (exact)
select '1.size', 'ingredients', count(*) from ingredients
union all select '1.size', 'ingredient_aliases', count(*) from ingredient_aliases
union all select '1.size', 'ingredient_localizations', count(*) from ingredient_localizations
union all select '1.size', 'products', count(*) from products
union all select '1.size', 'product_sources', count(*) from product_sources;

-- 2. Catalog composition
select '2.composition', source, verification_status, (id like 'synth\_%') as synthetic_id,
       count(*) from ingredients group by 2, 3, 4 order by 2, 3, 4;

-- 3. Field population (non-empty after trim), curated vs OCR-derived
with g as (select *, (id like 'synth\_%') as synthetic from ingredients)
select '3.fields', synthetic, count(*) as rows,
  count(*) filter (where coalesce(trim(e_number), '') <> '') as e_number,
  count(*) filter (where coalesce(trim(scientific_name), '') <> '') as scientific_name,
  count(*) filter (where coalesce(trim(category), '') <> '') as category,
  count(*) filter (where coalesce(trim(description), '') <> '') as description,
  count(*) filter (where coalesce(trim(purpose_in_food), '') <> '') as purpose_in_food,
  count(*) filter (where coalesce(trim(health_concerns), '') <> '') as health_concerns,
  count(*) filter (where coalesce(trim(side_effects), '') <> '') as side_effects,
  count(*) filter (where coalesce(trim(efsa_status), '') <> '') as efsa_status,
  count(*) filter (where coalesce(trim(acceptable_daily_intake), '') <> '') as adi,
  count(*) filter (where coalesce(trim("references"), '') <> '') as references_text,
  count(*) filter (where coalesce(trim(source_url), '') <> '') as source_url,
  count(*) filter (where retrieved_at is not null) as retrieved_at,
  count(*) filter (where coalesce(field_provenance_json, '') not in ('', '{}', 'null')) as field_provenance,
  count(*) filter (where risk_assessment_available) as risk_assessment_available
from g group by synthetic order by synthetic;

-- 4. E-number identity
select '4.e_number', 'distinct upper(e_number)', count(distinct upper(e_number))
  from ingredients where coalesce(trim(e_number), '') <> ''
union all select '4.e_number', 'e_numbers on more than one row', count(*) from (
  select upper(e_number) from ingredients where coalesce(trim(e_number), '') <> ''
  group by 1 having count(*) > 1) d
union all select '4.e_number', 'rows sharing an e_number with another row', coalesce(sum(n), 0) from (
  select count(*) n from ingredients where coalesce(trim(e_number), '') <> ''
  group by upper(e_number) having count(*) > 1) d
union all select '4.e_number', 'e_numbers only on OCR-derived rows (no curated row)', count(*) from (
  select upper(e_number) e from ingredients where coalesce(trim(e_number), '') <> ''
  group by 1 having bool_and(id like 'synth\_%')) d
union all select '4.e_number', 'e_numbers on a curated row', count(distinct upper(e_number))
  from ingredients where coalesce(trim(e_number), '') <> '' and id not like 'synth\_%'
union all select '4.e_number', 'curated rows with e_number and empty description', count(*)
  from ingredients where coalesce(trim(e_number), '') <> '' and id not like 'synth\_%'
   and coalesce(trim(description), '') = ''
union all select '4.e_number', 'e_number not matching ^E[0-9]{3,4}[A-Z]?$', count(*)
  from ingredients where coalesce(trim(e_number), '') <> '' and upper(e_number) !~ '^E[0-9]{3,4}[A-Z]?$';

-- 4b. E-numbers seen only on OCR-derived rows (codes, no free text)
select '4b.e_only_ocr', upper(e_number), count(*) as rows
  from ingredients where coalesce(trim(e_number), '') <> ''
  group by upper(e_number) having bool_and(id like 'synth\_%') order by 2;

-- 5. Identity uncertainty
select '5.identity', identity_uncertain, count(*) from ingredients group by 2 order by 2;
select '5.identity_reason', coalesce(nullif(trim(uncertainty_reason), ''), '(none)'), count(*)
  from ingredients where identity_uncertain group by 2 order by 3 desc limit 10;

-- 6. Aliases
select '6.alias', language, source, count(*) from ingredient_aliases group by 2, 3 order by 2, 3;
select '6.alias_ambiguous', 'alias_normalized values mapped to >1 ingredient', count(*) from (
  select alias_normalized from ingredient_aliases group by 1 having count(distinct ingredient_id) > 1) d;
select '6.alias_orphans', 'aliases whose ingredient is missing', count(*) from ingredient_aliases a
  where not exists (select 1 from ingredients i where i.id = a.ingredient_id);

-- 7. Localizations
select '7.localization', language, translation_status, translation_source,
       count(*) as rows, count(*) filter (where reviewed_at is not null) as reviewed_at_set
  from ingredient_localizations group by 2, 3, 4 order by 2, 3, 4;
select '7.localization_orphans', 'localizations whose ingredient is missing', count(*)
  from ingredient_localizations l where not exists (select 1 from ingredients i where i.id = l.ingredient_id);
select '7.localization_coverage', 'curated rows with a bg localization', count(*)
  from ingredients i where i.id not like 'synth\_%'
   and exists (select 1 from ingredient_localizations l where l.ingredient_id = i.id and l.language = 'bg');

-- 8. Product references (ids only)
with refs as (
  select p.barcode, trim(x) as ingredient_id
  from products p, unnest(string_to_array(coalesce(p.ingredient_ids, ''), ',')) as x
  where trim(x) <> '')
select '8.refs', 'products', (select count(*) from products)
union all select '8.refs', 'products with >=1 ingredient id', count(distinct barcode) from refs
union all select '8.refs', 'reference rows', count(*) from refs
union all select '8.refs', 'distinct referenced ids', count(distinct ingredient_id) from refs
union all select '8.refs', 'dangling ids (no ingredient row)', count(distinct ingredient_id) from refs r
  where not exists (select 1 from ingredients i where i.id = r.ingredient_id)
union all select '8.refs', 'reference rows to OCR-derived ingredients', count(*) from refs
  where ingredient_id like 'synth\_%'
union all select '8.refs', 'distinct OCR-derived ingredients referenced', count(distinct ingredient_id) from refs
  where ingredient_id like 'synth\_%'
union all select '8.refs', 'OCR-derived ingredients referenced by no product', count(*) from ingredients i
  where i.id like 'synth\_%' and not exists (select 1 from refs r where r.ingredient_id = i.id);

-- 9. Most-referenced OCR-derived ingredients (catalog names only), top 25
with refs as (
  select p.barcode, trim(x) as ingredient_id
  from products p, unnest(string_to_array(coalesce(p.ingredient_ids, ''), ',')) as x
  where trim(x) <> '')
select '9.top_ocr_ingredient', i.common_name, coalesce(nullif(i.e_number, ''), '-'),
       count(distinct r.barcode) as products
  from refs r join ingredients i on i.id = r.ingredient_id
  where i.id like 'synth\_%'
  group by i.common_name, i.e_number order by 4 desc, 2 limit 25;

-- 10. Name-quality flags on OCR-derived rows
select '10.quality', flag, count(*) from (
  select case
      when length(common_name) - length(replace(common_name, '(', '')) <>
           length(common_name) - length(replace(common_name, ')', '')) then 'unbalanced parenthesis'
      when common_name ~ '^[^[:alpha:]]+$' then 'no letters'
      when length(common_name) > 60 then 'longer than 60 characters'
      when common_name ~ '^\s|\s$' then 'leading/trailing whitespace'
      when common_name ~ '[0-9]{4,}' then 'contains a 4+ digit run'
    end as flag
  from ingredients where id like 'synth\_%') q
  where flag is not null group by flag order by 3 desc;
select '10.quality', 'OCR-derived rows sharing a normalized_name', coalesce(sum(n), 0) from (
  select count(*) n from ingredients where id like 'synth\_%'
  group by normalized_name having count(*) > 1) d;
select '10.quality', 'OCR-derived rows with a Cyrillic name', count(*)
  from ingredients where id like 'synth\_%' and common_name ~ '[А-Яа-я]';

ROLLBACK;
