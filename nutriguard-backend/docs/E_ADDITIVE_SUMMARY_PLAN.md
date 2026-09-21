# E-additive summaries -- Phase 1: inventory, sample pairs and API/data contract

Issue: #23 (`feat(backend): source-backed EN/BG summaries for existing E-additive catalog`).
Phase: **1 of 2 -- STOP at the sample/contract checkpoint for owner approval.** No bulk content,
no schema change, no loader change and no API change has been made. Backend only.

- Baseline: `origin/main` = `32bd7efc05750470da6f48eab7c4111e45db1d26` (verified with `git fetch` at the start of this phase).
- Branch: `feat/backend-e-additive-summaries`. It does **not** include PR #22 (see section 8 for the one genuine interaction).
- Author of all text below: Claude Code. Nothing here is reviewed by a human yet.
- Nothing was run against a live database or container; the only inputs are files in the repository and public web pages.

## 1. What exists today (inventory)

Read from the actual files at the baseline SHA: `app/seed/ingredients_seed.json`,
`app/seed/e_additives_curated_starter.csv`, `app/seed/ingredients_seed_bg.json`,
`app/seed/load_seed.py`, `app/services/ingredient_localization.py`, `app/models/ingredient*.py`,
`app/schemas/ingredient.py`.

| Source | Rows | Distinct E-codes | Notes |
|---|---|---|---|
| `ingredients_seed.json` (primary catalog) | 12 | 10 | 2 rows have no E-code (`high_fructose_corn_syrup`, `whole_oat_flour`). `VERIFIED` / `CURATED_SEED` / `risk_assessment_available=true`. |
| `e_additives_curated_starter.csv` | 43 | 43 | No duplicates. All rows `Curated starter`, confidence `High` (27) or `Medium` (16), `last_reviewed` 2026-09-08. Loaded as `LIMITED_DATA`, no risk assessment. |
| `ingredients_seed_bg.json` | 12 | -- | One BG display profile per primary row (all 12), none for any CSV-only row. |

**Target set (per issue): the union of E-codes already in the primary catalog and the populated starter CSV = 45 distinct E-codes.**
Overlap 8 (E250, E320, E322, E415, E471, E621, E951, E960); primary-only 2 (E102, E171); CSV-only 35.
The 800-row registry is not read or shipped and is not part of the target (placeholders do not establish valid additives).

### 1.1 Field population (what the data can already say)

* The CSV has **no origin/manufacture column** at all, so sentence 1 of the summary ("what it is and where it usually comes from")
  has no structured input for any CSV-only code. Primary rows have `scientificName` / `description` only.
* CSV optional fact columns (`digestion_absorption`, `metabolism`, `potential_effects`, `human_evidence`, `animal_evidence`,
  `adi_tdi`, `efsa_assessment`, `jecfa_assessment`, `eu_regulatory_note`): 18 rows have none, 13 have exactly one, 12 have two or more.
  `animal_evidence` is empty in all 43 rows. 11 rows have *only* an ADI. Rows with no optional fact: E306, E307, E320, E321, E322, E331,
  E338, E410, E412, E415, E420, E433, E440, E460, E471, E500, E627, E631.
* Only 3 CSV rows (E249, E250, E951) carry an `efsa_assessment`/`jecfa_assessment` text. For E249/E250 those texts are instructions ("Use latest
  EFSA nitrite/nitrate re-evaluation...", "JECFA ADI applies; verify current database entry"), not findings. E951 states findings ("EFSA established
  ADI: 40 mg/kg bw/day", "JECFA reaffirmed ADI 0-40 mg/kg bw/day in 2023") but names no document.
* Only 1 row (E951) has an `eu_regulatory_note` ("Phenylalanine-related labelling applies under EU rules"), again with no document.
* Localization: BG display text exists for the 10 primary E-codes (12 rows) and for **none of the 35 CSV-only** codes. The BG rows are
  `REVIEWED` + `MACHINE_TRANSLATED` (the loader stamps `REVIEWED` itself; a human review of the wording is not recorded anywhere).

### 1.2 Source specificity

**All 43 CSV rows cite the identical four generic landing pages** (EFSA "food additives" topic, the JECFA database home, the Codex
GSFA database home, the EU Commission "additives" page) and the same review date. None of them identifies a specific
opinion, monograph or regulation, so `primary_sources` cannot support any individual claim. Primary rows cite specific documents
(e.g. `EFSA Journal 2021;19(5):6585`) but several claims on those rows go beyond the cited document (section 2.1).
`CURATED_SEED` / `VERIFIED` are therefore treated as *provenance labels*, not proof.

### 1.3 Uncertain generic-vs-specific identities

* **E322**: primary row = `e322_soy_lecithin` ("Soy Lecithin", allergen "Soy"); CSV row = generic "Lecithins". The loader skips the CSV row
  because the E-number already exists, and `ingredients.e_number` is `UNIQUE`, so two rows cannot share E322. A generic E322 summary must
  not become "soy lecithin" and the soy-specific row must not be described as the whole family.
* **E471** (source-dependent: the primary row says "derived from animal or plant fats"), **E960** (primary "Steviol Glycosides (Stevia)" vs CSV "Steviol glycosides"),
  **E331 / E500 / E952 / E954** (the CSV names are plural -- "Sodium citrates", "Sodium carbonates", "Cyclamates", "Saccharins" -- i.e. a family of related
  salts/forms under one number; the exact members were not checked here), **E306** ("Tocopherol-rich extract", a mixture), **E407** (the CSV note reads only
  "Critical literature-screening distinction." and does not say what is distinguished; it must be resolved from a source, not guessed): the summary must
  describe the E-code generically and never one member/source.

### 1.4 Inventory by E-code

| E-code | In primary JSON | In starter CSV | Name(s) | CSV facts present (of 8 optional) | BG display text | Identity note |
|---|---|---|---|---|---|---|
| E102 | yes | no | Tartrazine (Yellow 5) | – | yes (`e102_tartrazine`) |  |
| E171 | yes | no | Titanium Dioxide | – | yes (`e171_titanium_dioxide`) | primary only; regulatory status changed (EU withdrawal 2022) |
| E200 | no | yes | Sorbic acid | 1 | no |  |
| E202 | no | yes | Potassium sorbate | 1 | no |  |
| E210 | no | yes | Benzoic acid | 1 | no |  |
| E211 | no | yes | Sodium benzoate | 1 | no |  |
| E220 | no | yes | Sulfur dioxide | 2 | no | CSV states a group ADI that EFSA has since withdrawn (see section 2.1) |
| E221 | no | yes | Sodium sulfite | 2 | no | group with E220 (EFSA group opinion); CSV states the same group ADI, which EFSA has since withdrawn (see section 2.1) |
| E223 | no | yes | Sodium metabisulfite | 2 | no | group with E220 (EFSA group opinion); CSV states the same group ADI, which EFSA has since withdrawn (see section 2.1) |
| E249 | no | yes | Potassium nitrite | 7 | no |  |
| E250 | yes | yes | Sodium Nitrite | 7 | yes (`e250_sodium_nitrite`) |  |
| E251 | no | yes | Sodium nitrate | 4 | no |  |
| E252 | no | yes | Potassium nitrate | 4 | no |  |
| E260 | no | yes | Acetic acid | 1 | no |  |
| E270 | no | yes | Lactic acid | 1 | no |  |
| E300 | no | yes | Ascorbic acid | 1 | no |  |
| E301 | no | yes | Sodium ascorbate | 1 | no |  |
| E306 | no | yes | Tocopherol-rich extract | 0 | no | tocopherol-rich extract (mixture) |
| E307 | no | yes | Alpha-tocopherol | 0 | no |  |
| E320 | yes | yes | Butylated Hydroxyanisole (BHA) | 0 | yes (`e320_bha`) |  |
| E321 | no | yes | Butylated hydroxytoluene (BHT) | 0 | no |  |
| E322 | yes | yes | Soy Lecithin / Lecithins | 0 | yes (`e322_soy_lecithin`) | GENERIC-vs-SPECIFIC: primary row is soy-specific (`e322_soy_lecithin`), CSV row is generic "Lecithins" |
| E330 | no | yes | Citric acid | 3 | no |  |
| E331 | no | yes | Sodium citrates | 0 | no | plural name ("Sodium citrates"): a family of related salts; do not collapse to one salt |
| E338 | no | yes | Phosphoric acid | 0 | no |  |
| E407 | no | yes | Carrageenan | 1 | no | CSV note only says "Critical literature-screening distinction." (unspecified) |
| E410 | no | yes | Locust bean gum | 0 | no |  |
| E412 | no | yes | Guar gum | 0 | no |  |
| E415 | yes | yes | Xanthan Gum | 0 | yes (`e415_xanthan_gum`) |  |
| E420 | no | yes | Sorbitol | 0 | no |  |
| E433 | no | yes | Polysorbate 80 | 0 | no |  |
| E440 | no | yes | Pectins | 0 | no |  |
| E460 | no | yes | Cellulose | 0 | no |  |
| E466 | no | yes | Carboxymethyl cellulose (CMC) | 1 | no |  |
| E471 | yes | yes | Mono- and Diglycerides of Fatty Acids | 0 | yes (`e471_mono_diglycerides`) | source-dependent (animal or plant fats); primary flags corrected to unknown in open PR #22 |
| E500 | no | yes | Sodium carbonates | 0 | no | plural name ("Sodium carbonates"): a family of related salts |
| E621 | yes | yes | Monosodium Glutamate / Monosodium glutamate (MSG) | 4 | yes (`e621_msg`) |  |
| E627 | no | yes | Disodium guanylate | 0 | no |  |
| E631 | no | yes | Disodium inosinate | 0 | no |  |
| E950 | no | yes | Acesulfame K | 1 | no |  |
| E951 | yes | yes | Aspartame | 8 | yes (`e951_aspartame`) |  |
| E952 | no | yes | Cyclamates | 1 | no | plural name ("Cyclamates"): a family of related forms |
| E954 | no | yes | Saccharins | 1 | no | plural name ("Saccharins"): a family of related forms |
| E955 | no | yes | Sucralose | 3 | no |  |
| E960 | yes | yes | Steviol Glycosides (Stevia) / Steviol glycosides | 3 | yes (`stevia_extract`) | primary `stevia_extract` ("Steviol Glycosides (Stevia)") vs CSV "Steviol glycosides"; E 960 numbering may have been subdivided since (unverified lead, see section 2.1 item 6) |

## 2. Source verification (what was actually read)

Method: for each sample additive I looked for the specific EFSA opinion / WHO-IARC-JECFA statement / EU regulation and read it.
Direct Wiley (EFSA Journal) pages returned HTTP 403 and EUR-Lex pages returned no text to the fetch tool in this session, so open mirrors
(EFSA's own topic pages, NCBI PMC copies of the EFSA Journal articles, WHO news pages) were used. **A claim is marked
"read" only when the fetched page text stated it; "search-snippet" when only a search result summarised it; "catalog-only" when the
only support is our own seed/CSV.** The plan is to promote nothing to the served summary in Phase 2 unless it is "read".

| # | Additive | Claim | Document (specific) | Where | Status |
|---|---|---|---|---|---|
| 1 | E951 | ADI 40 mg/kg bw/day confirmed; typical exposure well below it; safe for general population; ADI not applicable to people with PKU (strict low-phenylalanine diet) | EFSA, *Scientific Opinion on the re-evaluation of aspartame (E 951) as a food additive*, EFSA Journal 2013;11(12):3496 (Dec 2013) | EFSA topic page https://www.efsa.europa.eu/en/topics/topic/aspartame (quotes the 2013 conclusions) | read |
| 1 | E951 | Rapidly hydrolysed to phenylalanine, aspartic acid and methanol | same opinion, abstract | search result text of https://www.efsa.europa.eu/it/efsajournal/pub/3496 | search-snippet |
| 1 | E951 | IARC 2023: "possibly carcinogenic to humans (Group 2B)" on *limited* evidence; JECFA 2023 reaffirmed ADI 0-40 mg/kg bw; IARC Monographs vol. 134 (6-13 Jun 2023), JECFA 96th meeting (27 Jun-6 Jul 2023) | WHO/IARC news item 14 Jul 2023 | https://www.who.int/news/item/14-07-2023-aspartame-hazard-and-risk-assessment-results-released | read |
| 2 | E410 | Derived from ground endosperm of carob (*Ceratonia siliqua*) seeds (as specified in Reg. (EU) 231/2012); no need for a numerical ADI; no safety concern for the general population | EFSA, *Re-evaluation of locust bean gum (E 410) as a food additive*, EFSA Journal 2017;15(1):4646 (20 Jan 2017) | PMC copy https://pmc.ncbi.nlm.nih.gov/articles/PMC7010100/ | read |
| 2 | E410 | Follow-up assessed use in foods for infants below 16 weeks (special formulae) and toddlers (FSMP); margins of exposure above 1 at reported levels | EFSA, EFSA Journal 2023;21(2):7775 (9 Feb 2023) | PMC copy https://pmc.ncbi.nlm.nih.gov/articles/PMC9909383/ | read |
| 2 | E410 | Function "thickener / stabiliser" | CSV `functional_class`; EFSA 2017 abstract text did not restate it | -- | catalog-only |
| 3 | E322 | Lecithins are mixtures/fractions of phosphatides obtained by physical procedures from animal or vegetable foodstuffs (the source is not fixed by the E-number) | Commission Regulation (EU) No 231/2012, specification for E 322 | search-result text only (the EUR-Lex / legislation.gov.uk pages did not return the specification text) | search-snippet |
| 3 | E322 | No need for a numerical ADI; no safety concern for the general population from >1 year; no concern for infants 12 weeks-11 months at reported uses | EFSA, *Re-evaluation of lecithins (E 322) as a food additive*, EFSA Journal 2017;15(4):4742 | search result text of https://www.efsa.europa.eu/en/efsajournal/pub/4742 (Wiley page 403) | search-snippet |
| 3 | E322 | A 2020 follow-up covers infants below 16 weeks (title seen; conclusions **not read**) | EFSA Journal 2020, article 6266 (volume/issue not confirmed) | -- | not read |
| 4 | E220 | EFSA **withdrew** the temporary group ADI (0.7 mg SO2 eq/kg bw/day) because the toxicity database was not adequate to derive an ADI; margins of exposure < 80 for all groups but adolescents; inadequate safety margins at maximum permitted levels | EFSA, *Follow-up of the re-evaluation of sulfur dioxide (E 220), sodium sulfite (E 221), ... potassium bisulfite (E 228)*, EFSA Journal 2022;20(11):7594 (24 Nov 2022) | PMC copy https://pmc.ncbi.nlm.nih.gov/articles/PMC9685353/ | read |
| 4 | E220 | Group E 220-228 evaluated together; 2016 opinion had kept 0.7 mg SO2 eq/kg bw as a *temporary* group ADI, with exposure above it for all groups | EFSA Journal 2016, article 4438 (volume/issue not confirmed) | EFSA search result text (page itself redirected to Wiley) | search-snippet |
| 4 | E220 | "Sulfite-sensitive individuals, including some people with asthma, can react" (CSV text) | none specific | -- | **catalog-only -> withheld from the sample** |
| 4 | E220 | Labelling threshold 10 mg/kg (Reg. (EU) 1169/2011 Annex II) | not retrievable in this session | -- | not read |
| 5 | E171 | 2021: "a concern for genotoxicity of TiO2 particles that may be present in E 171 could not be ruled out"; "can no longer be considered as safe when used as a food additive"; no ADI established | EFSA, *Safety assessment of titanium dioxide (E171) as a food additive*, EFSA Journal 2021;19(5):6585 (6 May 2021) | PMC copy https://pmc.ncbi.nlm.nih.gov/articles/PMC8101360/ | read |
| 5 | E171 | Authorisation withdrawn by Commission Regulation (EU) 2022/63 of 14 Jan 2022, in force 7 Feb 2022; 6-month transition to 7 Aug 2022, products already placed on the market may stay until minimum-durability/use-by date | Reg. (EU) 2022/63 | search-result text (EUR-Lex page returned no text to the tool) | search-snippet |
| 5 | E171 | Function "inorganic white pigment; whitens/opacifies" | seed row `e171_titanium_dioxide` | -- | catalog-only |

### 2.1 Outdated / conflicting material found (flagged, not silently resolved)

1. **E220-E228 group ADI.** The CSV rows for E220, E221 and E223 say "0-0.7 mg/kg bw/day as SO2 (group, JECFA)". EFSA **withdrew** its
   (temporary) group ADI in 2022 (read). Whether JECFA's own 0-0.7 figure still stands was **not** checked here. A summary must not state a
   current numeric limit without deciding which authority it cites; proposed handling: state the EFSA 2022 position and attribute any JECFA
   figure separately, only after JECFA's current entry is read.
2. **E171 catalog row over-states / mis-attributes.** `countriesRestrictedOrBanned` "Banned in EU (EFSA 2021)": EFSA assessed; the
   *withdrawal* is Reg. (EU) 2022/63. `sideEffects` "DNA damage potential, intestinal inflammation" asserts effects where EFSA's wording is
   "a concern ... could not be ruled out"; "Restricted in Switzerland" and FDA "allowed up to 1% by weight" cite nothing. Summaries must use the
   EFSA/Regulation wording and the row's stronger claims stay un-promoted.
3. **E951 catalog row.** `healthConcerns` "Potential impacts on gut microbiota and insulin sensitivity" and `sideEffects` "Headaches, mood
   alterations" are not in the EFSA 2013 / IARC / JECFA statements read here (they may be supported elsewhere -- not established).
4. **E322 generic vs soy** (section 1.3), and **E471** (primary text "Synthetic mixture ... derived from animal or plant fats", CSV "derived from
   glycerol + fatty acids"; open PR #22 already changes its dietary flags to unknown because the origin is not determinable).
5. **E320 BHA**: "Endocrine disruptor, suspected human carcinogen (IARC 2B)", "Restricted in Japan, UK infant foods" -- strong or jurisdiction claims
   with no specific citation; not verified here.
6. **E960 (lead only, not verified):** the EU numbering for steviol glycosides may have been subdivided into lettered sub-numbers since the CSV/seed were
   written (from memory, not from a source). I have not read the regulation; Phase 2 must check before writing any E 960 text.

## 3. Five representative EN/BG summary pairs

Shape (per issue): 2-3 short sentences. (1) what it is and its usual, *generic* origin/manufacture where known; (2) why it is used in food;
(3) supported effects/conditions only, separating dose/exposure, sensitivity and uncertainty. Statements describe the **E-number
generically** and never assert what is in the scanned product. No safe-intake advice, risk score or "harmful" claim is invented; a limit
appears only when an authority states it and is attributed. Identifiers (E-codes, numbers, units, years, regulation numbers, organisation
names) are kept unchanged in BG. Empty facts stay absent (no filler).

**All BG text below is my own machine translation: `translationSource=MACHINE_TRANSLATED`, `translationStatus=DRAFT`. It is
not scientifically verified and not REVIEWED. Per the existing localization gate a DRAFT BG text would not be served.**

### 3.1 E322 Lecithins -- source-dependent, generic (not "soy lecithin")

**EN** (3 sentences, 394 chars)
> Lecithins (E 322) are mixtures of phospholipids obtained from animal or vegetable foods; the raw material differs between products and is not shown by the E-number alone. They are used as emulsifiers, helping fat and water-based ingredients stay mixed. EFSA (2017) found no need for a numerical acceptable daily intake and no safety concern for people over one year of age at the reported uses.

**BG**
> Лецитините (E 322) са смеси от фосфолипиди, получавани от животински или растителни храни; суровината се различава между продуктите и не личи само от E-номера. Използват се като емулгатори – помагат на мазнините и водните съставки да останат смесени. EFSA (2017) не вижда нужда от числено определен допустим дневен прием и не установява риск за безопасността при отчетените употреби за хора над една година.

Support: sentence 1 Reg. 231/2012 (search-snippet, must be read before Phase 2); 2 catalog-only (CSV class "Emulsifier"); 3 EFSA 2017 (search-snippet).
Gate: the summary attaches to the *E-number*; the soy-specific row keeps its own soy-specific fields. Deliberately silent on soy/egg/sunflower.

### 3.2 E410 Locust bean gum -- sparsely populated row (CSV has only identity + role)

**EN** (3 sentences, 377 chars)
> Locust bean gum (E 410) is a thickener and stabiliser derived from the ground endosperm of carob tree (*Ceratonia siliqua*) seeds. It is used to give foods a thicker, more stable texture. EFSA (2017) concluded that no numerical acceptable daily intake is needed and found no safety concern for the general population; a 2023 follow-up assessed its use in special infant formulae.

**BG**
> Гумата от рожков (E 410) е сгъстител и стабилизатор, получаван от смляния ендосперм на семената на рожковото дърво (*Ceratonia siliqua*). Използва се, за да придаде на храните по-гъста и по-стабилна консистенция. EFSA (2017) заключава, че не е необходим числено определен допустим дневен прием и не установява риск за безопасността за общото население; последващо становище от 2023 г. оценява употребата ѝ в специални млека за кърмачета.

Support: 1 EFSA 2017 (read) + function catalog-only; 2 catalog-only; 3 EFSA 2017 and EFSA 2023 (read). This row shows that a
near-empty CSV row still yields a fully source-backed summary **only because** the source was read -- the CSV itself supports none of it.

### 3.3 E951 Aspartame -- well described, with dose and a sensitive group

**EN** (3 sentences, 470 chars)
> Aspartame (E 951) is a high-intensity artificial sweetener; in the body it is broken down into phenylalanine, aspartic acid and methanol. It is used in small amounts to sweeten foods and drinks. EFSA (2013) and JECFA (2023) keep the daily limit at 40 mg per kg of body weight, with typical intake far below it; IARC (2023) rates it "possibly carcinogenic" on limited evidence, and the limit does not apply to people with phenylketonuria, who must restrict phenylalanine.

**BG**
> Аспартамът (E 951) е високоинтензивен изкуствен подсладител; в организма се разгражда до фенилаланин, аспарагинова киселина и метанол. Използва се в малки количества за подслаждане на храни и напитки. EFSA (2013) и JECFA (2023) потвърждават допустимия дневен прием от 40 mg на kg телесно тегло, като типичният прием е далеч под него; IARC (2023) го класифицира като „възможно канцерогенен“ при ограничени доказателства, а лимитът не важи за хора с фенилкетонурия, които трябва да ограничават фенилаланина.

Support: 1 EFSA 2013 (search-snippet for hydrolysis); 2 catalog-only; 3 EFSA topic page, WHO/IARC news 14 Jul 2023 (read). "Typical intake far below"
is EFSA's 2013 estimate, not a 2026 exposure figure -- the wording must carry the year in Phase 2. Length is the outlier (see Q4).

### 3.4 E220 Sulfur dioxide -- outdated catalog value, sensitivity claim withheld

**EN** (3 sentences, 412 chars)
> Sulfur dioxide (E 220) is a gas used as a preservative and antioxidant; the related sulfites E 221-228 are assessed with it as a group. It slows spoilage by microbes and by oxidation. In 2022 EFSA withdrew the earlier group acceptable daily intake of 0.7 mg SO2 equivalents per kg of body weight because the toxicity data were not adequate, and reported inadequate safety margins at the maximum permitted levels.

**BG**
> Серният диоксид (E 220) е газ, използван като консервант и антиоксидант; сродните сулфити E 221–228 се оценяват заедно с него като група. Забавя развалянето от микроорганизми и от окисляване. През 2022 г. EFSA оттегли предишния групов допустим дневен прием от 0,7 mg SO2 еквиваленти на kg телесно тегло, защото данните за токсичност не бяха достатъчни, и отчете недостатъчни граници на безопасност при максимално разрешените нива.

Support: 1 group = EFSA 2016/2022 titles (read/search-snippet), functions catalog-only; 2 catalog-only; 3 EFSA 2022 (read).
**Withheld on purpose:** "sulfite-sensitive people, including some with asthma, can react" (in the CSV, no specific source read) and the labelling
threshold. They would be appended as a fourth clause the moment a specific document is read -- this example shows source-gating, and shows that
the CSV's ADI is *not* copied into the summary.

### 3.5 E171 Titanium dioxide -- regulatory status is jurisdiction- and date-dependent

**EN** (3 sentences, 402 chars)
> Titanium dioxide (E 171) is an inorganic white pigment. It is used as a food colour to make products look whiter and more opaque. In 2021 EFSA concluded that a genotoxicity concern for its particles could not be ruled out, so it could no longer be considered safe as a food additive; the EU withdrew its authorisation for food in 2022 (Regulation (EU) 2022/63), and this describes the EU position only.

**BG**
> Титановият диоксид (E 171) е неорганичен бял пигмент. Използва се като оцветител, за да изглеждат продуктите по-бели и по-непрозрачни. През 2021 г. EFSA заключава, че не може да се изключи опасение за генотоксичност на частиците му и затова той вече не може да се счита за безопасен като хранителна добавка; ЕС отне разрешението му за храни през 2022 г. (Регламент (ЕС) 2022/63), като това описва само позицията в ЕС.

Support: 1-2 catalog-only (needs a specific source); 3 EFSA 2021 (read), Reg. 2022/63 (search-snippet). Uses EFSA's own wording ("concern ... could not be
ruled out"), *not* the catalog's "DNA damage potential".

### 3.6 What the five examples are meant to settle

| Property | Shown by |
|---|---|
| source-dependent, generic (no invented origin, no soy) | E322 |
| sparsely populated row still supported (by reading the specific opinion) | E410 |
| well-described row incl. dose (attributed limit, year) and sensitive group | E951 |
| catalog value found outdated; unsupported clause withheld | E220 |
| regulatory status scoped to jurisdiction/date; catalog over-statement not copied | E171 |
| empty field absent, no filler | E220 (no sensitivity clause), see JSON below for an uncovered code |

## 4. Proposed minimal API / data contract (for approval)

### 4.1 Data model -- a new table, keyed by E-number identity

`ingredient_summaries` (one row per `(e_number, language)`), **not** new columns on `ingredients` / `ingredient_localizations`.

| Column | Meaning |
|---|---|
| `e_number` (PK part) | canonical identity, e.g. `E322`. Not `ingredient_id`, because two catalog rows can never share an E-number and the summary is generic to the E-number. |
| `language` (PK part) | `en` / `bg` (same CHECK as `ingredient_localizations`). |
| `text` | the 2-3 sentence summary. |
| `citations_json` | ordered list of `{id, label, url, documentDate, section, supports:["origin"|"function"|"effects"|"regulatory"]}`. Language-independent; `bg` rows reference the `en` citation ids. |
| `evidence_state` (`en` only) | `DRAFT` -> `SOURCE_VERIFIED` (every sentence has a *read* source, independent second check recorded). Only `SOURCE_VERIFIED` is served. |
| `translation_status` / `translation_source` (`bg` only) | reuse the existing enums (`DRAFT`/`REVIEWED`, `MACHINE_TRANSLATED`/`HUMAN_CURATED`). A machine translation is **never** set to `REVIEWED` by the loader. |
| `source_content_hash` (`bg` only) | sha256 of the `en` text + citation ids. A changed EN text makes the BG row stale and unserved, as today. |
| `raw_source_json` | per-sentence claim -> quoted source passage / page section, for maintenance and audit (not served). |
| `field_provenance` / `reviewed_at` / `schema_version` | per-field provenance in the style of `field_provenance_json`; timestamps. |

Why a separate table: `canonical_text_hash` covers *every* localized field, so adding `summary` to `LOCALIZED_FIELDS` would change the hash of all
12 existing reviewed BG rows and make the API silently drop them (falling back to English); a separate table leaves them untouched. Keying by E-number
also removes the E322 collision (the generic summary is served on the soy-specific row's payload as a family-level description, next to its own
soy-specific fields).

### 4.2 Wire contract (additive; no existing field changes; camelCase)

Added to `IngredientOut` and to each `localizations.<lang>` profile. **Every new field is nullable/omittable; absence means "no source-verified summary".**

```jsonc
// GET /api/v1/ingredients/{id} and every IngredientOut embedded in a scan/product response
{
  "eNumber": "E951",
  // ...all existing fields unchanged...
  "summary": {                              // null / absent when none is SOURCE_VERIFIED
    "text": "Aspartame (E 951) is a high-intensity artificial sweetener; ...",   // canonical English
    "scope": "E_NUMBER_GENERIC",            // constant today; states this is not a claim about the scanned product
    "evidenceState": "SOURCE_VERIFIED",
    "reviewedAt": 1790000000000,            // epoch ms, like the other timestamps
    "citations": [
      { "id": "efsa-2013-e951", "label": "EFSA (2013), EFSA Journal 11(12):3496",
        "url": "https://www.efsa.europa.eu/it/efsajournal/pub/3496", "documentDate": "2013-12", "supports": ["function", "effects"] },
      { "id": "who-iarc-2023-aspartame", "label": "WHO/IARC/JECFA (14 Jul 2023)",
        "url": "https://www.who.int/news/item/14-07-2023-aspartame-hazard-and-risk-assessment-results-released", "documentDate": "2023-07-14", "supports": ["effects"] }
    ]
  },
  "localizations": {
    "en": { /* existing profile */ "summary": { "text": "..." } },
    "bg": { /* present only if REVIEWED and hash-current, exactly as today */ "summary": { "text": "Аспартамът (E 951) е ..." } }
  }
}
// an additive with no verified summary (e.g. E306): "summary": null, "localizations.*.summary" omitted -- NEVER a filler string
```

Semantics for Codex/Android (Android is not touched here):
* Show the compact text directly under the ingredient name / E-code in the selected language; if `localizations.<lang>.summary` is absent use the
  English `summary.text` (same fallback the existing profile uses). Citations are always the top-level `summary.citations`, rendered at the bottom of the
  detail view; hide the whole block when `summary` is null.
* Persist the whole `summary` object for offline display.
* **Colour never follows the E-number or the summary.** Colour keeps following `riskAssessmentAvailable` + `riskLevel` (an independent assessment). A
  summary that says "concern could not be ruled out" (E171) carries no colour of its own.
* Do not show `scope`/`evidenceState` to users; they exist so a client can refuse an unknown future scope.
* No per-scan lookup or generation: summaries are stored and shipped with the ingredient payload.

`openapi.json` will gain the new optional fields (regenerated and diffed in Phase 2; widening only).

## 5. Phase 2 plan (not started; needs approval of sections 3-4)

1. **Content**: for each of the 45 E-codes read the specific EFSA opinion (and JECFA monograph / EU regulation where the claim needs one), fill a
   per-sentence support table like section 2, draft EN, then an *independent* reviewer subagent re-checks every claim against the quoted passage
   ("SOURCE_VERIFIED" only after that). Report every code that cannot be supported (an "uncovered" list) instead of writing filler; a code may get
   1-2 sentences if only those are supported.
2. **Fill-missing loader**: replace the "skip the whole row if the E-number exists" behaviour by per-field fill with per-field provenance:
   write a field only when it is empty, never overwrite a non-empty/better-supported value, record the source and content hash so a repeated load
   is a no-op. Generic E-code families are never collapsed into a specific source or allergen (E322 stays generic; only the existing soy row is soy).
   Idempotent seed loading, no duplicates. **Never run against the live database.**
3. **BG**: machine translation drafted by me (identifier-preserving); automated checks (E-codes / numbers / units / years / citation ids are
   byte-identical, sentence count, no added claims via a back-translation comparison by a separate reviewer). Status stays `DRAFT`/
   `MACHINE_TRANSLATED`. It becomes `REVIEWED` only after a human Bulgarian-reading reviewer signs off; until then BG is simply not served (EN fallback).
   Machine translation confers neither scientific verification nor review status.
4. **Tests** (pinned suite + disposable PostgreSQL): safe fill-missing; richer-field preservation; repeated loads / no duplicates; exact E-code
   identity; generic-vs-specific identity (E322, E471, E960); missing content -> absent; EN/BG fallback; review-state and hash gating (a changed EN text
   unserves BG). If the schema changes: disposable-PostgreSQL upgrade -> downgrade -> upgrade with representative data/link preservation, single Alembic
   head, pinned OpenAPI regeneration.
5. **Report** in committed docs: exact baseline/head SHAs, files, coverage counts, tests run/skipped, API/migration implications, source gaps, risks.

Rough coverage expectation (an estimate, not a promise): the 6-8 codes with a specific EFSA re-evaluation already cited in the primary catalog can be finished
first; the 35 CSV-only codes each need their own document read. Nothing will be back-filled to reach 100%.

## 6. Open questions for the owner (answers needed before Phase 2)

1. **Keying / E322**: approve a summary table keyed by E-number (generic family text served on whichever catalog row holds that E-number), rather than columns on `ingredients`?
2. **Serving rule**: serve EN only when `SOURCE_VERIFIED`, and BG only when `REVIEWED` *and* hash-current (existing rule) -- so BG will be absent for all 45 until a human review is arranged. Acceptable? If a Bulgarian reviewer is available, who?
3. **Jurisdiction wording** (E171 style): is "this describes the EU position only" the right pattern for regulatory sentences, or should regulatory status stay out of the 2-3 sentence text and only be carried by the existing structured fields?
4. **Length**: samples run 377-470 characters. Is ~450 characters (with expand) the Android budget, or should sentence 3 for well-described rows (E951) be cut to the limit + one caveat?
5. **Outdated E220-228 ADI**: prefer (a) drop the numeric limit from the summary and cite the EFSA 2022 position (as in the sample), or (b) also show a JECFA figure once JECFA's current entry has been read?
6. **Existing structured fields**: the over-statements listed in section 2.1 (E171/E951/E320 rows) live in the primary catalog. Out of scope for this task unless you want them corrected as part of Phase 2 -- I have not changed them.

## 7. Risks / limits stated plainly

* The samples are drafts. Four of the five have at least one sentence supported only by catalog text or a search snippet (marked above); none is human-reviewed.
* Source access was partial (Wiley 403, EUR-Lex empty). Statuses in section 2 are literal about what was read.
* Coverage will probably be below 100% of the 45; uncovered codes will be listed, not padded.
* Regulatory statements go stale; every jurisdictional sentence carries a year and a scope, and `reviewedAt` allows re-review.

## 8. Interaction with PR #22 (documented, not stacked)

PR #22 (`feat/backend-truthful-unknowns-diagnostics`, unmerged) changes `ingredients_seed.json` (E471 dietary flags -> unknown), `IngredientOut`
(no nullable-flag change on ingredients, but `ProductOut` flags become nullable) and `openapi.json`. This feature is built on `origin/main` and
touches none of those in Phase 1. In Phase 2 the only shared surface is `openapi.json` (additive fields on `IngredientOut`) and, if E322 handling
needs it, `ingredients_seed.json`; whichever PR merges second must regenerate `openapi.json` (a mechanical conflict). No genuine functional
dependency, so no stacking is proposed.
