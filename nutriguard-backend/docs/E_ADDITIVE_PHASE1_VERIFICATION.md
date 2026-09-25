# E-additives: E150d investigation and phase-1 source verification (issue #23)

Follow-up to `docs/E_ADDITIVE_SUMMARY_PLAN.md` (phase-1 checkpoint, commit
`d0c5c27c9ac990e88379da9552cb047061f81713`), answering the owner's request
of 2026-09-25 (issue #23 comment 5832581010). Branch
`feat/backend-e-additive-summaries`. Baseline `origin/main` =
`32bd7efc05750470da6f48eab7c4111e45db1d26`.

**This is still the phase-1 sample/contract checkpoint.** This commit is
documentation only: no code, seed, schema, API or loader change. No bulk
content has been written. No live database, container or log was
accessed, and nothing was merged or deployed. Every BG text below is a
DRAFT machine translation, is not reviewed, and would not be served
under the existing gate.

## Part A: E150d shows only as a code

### A.1 Conclusion, separated as requested

| Hypothesis | Finding | Evidence |
|---|---|---|
| **Content absent** | **Yes: the primary cause.** No catalog row, starter-CSV row, BG profile or alias exists for E150d (or any E150 variant, or "caramel") anywhere in the repository. | `git grep -i "e150\|150d\|caramel"` over the branch: no hit in code or data. The same search over every `origin/*` branch finds nothing. `e_additives_curated_starter.csv` has 43 codes and E150d is not one of them. E150d is also not in the 45-code phase-1 target set, which is the union of the primary catalog and the starter CSV. |
| **Not loaded** | **No.** There is no E150d content anywhere that fails to get loaded. The only unloaded source is the 800-row registry named in `load_seed.py`, which is deliberately not shipped (it is not in the repository), and its non-starter rows are coverage placeholders, not content. | `app/seed/load_seed.py` `_load_e_additive_starter` docstring; repository contents. |
| **Mismatched** | **Not the cause for a normal "E150d" token, but real matching gaps exist (A.3).** A plain `E150d` / `E 150d` / `E-150d` token is correctly canonicalized to `E150D` and looked up by exact E-number. There is nothing to match, so an unmatched synthetic row is created. | `ocr_normalizer._E_NUMBER` = `e[- ]?(\d{3,4}[a-z]?)`; reproduction in A.2. |
| **Filtered** | **No.** `IngredientOut` serializes `description`/`purposeInFood`/`category` as stored; only derived regulatory/ADI fields are gated. The synthetic row is empty because OCR-only rows deliberately carry no scientific text (`create_synthetic_ingredient`: "OCR is provenance, not scientific evidence"). | `app/schemas/ingredient.py`; `app/services/ocr_normalizer.py`. |

**What this does and does not prove.** The code and data at `main` cannot
show E150d content, because none exists. That is consistent with the
owner's phone screenshot, but **the screenshot alone does not prove a
catalog failure**, and the live database was not inspected. On the live
system, E150d would normally be a synthetic `synth_…` row whose
`commonName` is whatever OCR text first created it (A.3 item 4).
Confirming that needs a read-only look at the live row, which was not
authorized here.

### A.2 Synthetic reproduction (actual API JSON, not committed)

Setup: the in-memory test database, with the real starter CSV loaded
through `load_seed._load_e_additive_starter` (43 rows inserted). Each
spelling was sent to `POST /api/v1/scan/ocr-text` in sequence, on
branch code identical to `main`. Relevant ingredient fields:

| OCR text | Resulting ingredient |
|---|---|
| `Water, Colour (E150d), Citric Acid (E330)` | `E330` → curated starter row `e330_citric_acid` with a real `description`. `E150d` → **new synthetic** `synth_colour_e150d_…`, `commonName: "Colour (E150d"`, `eNumber: "E150D"`, `description: ""`, `category: "Food Additive (E150D)"`, `UNVERIFIED`/`OCR_HEURISTIC` |
| `E150d` | **the same** synthetic row (`commonName` still `"Colour (E150d"`) |
| `Caramel colour E 150d` | same synthetic row |
| `E-150d` | same synthetic row |
| `карамел (E150d)` | same synthetic row |
| `E150 d` (space before the letter) | **different** synthetic row `synth_e150_d_…`, `eNumber: "E150"` |
| `Е150d` (Cyrillic **Е**, U+0415) | synthetic `synth_150d_…`, **`eNumber: null`**, not recognized as an E-number |
| `Caramel colour` / `Sulphite ammonia caramel` | synthetic rows, `eNumber: null` |

The E330 row next to it shows the difference: a code that has catalog
content returns it through the same path.

### A.3 Matching and display gaps found (reported, not fixed here)

1. **Cyrillic "Е" is not recognized as an E-number prefix.** The E-number
   patterns (`ocr_normalizer._E_NUMBER`,
   `label_language._E_NUMBER_RE`, `ingredient_translation._E_NUMBER_RE`)
   accept only the Latin letter `E`. Bulgarian labels frequently print
   Cyrillic `Е`. Such a token gets no `eNumber` and can never match a
   catalog row, even for codes that have content.
2. **A space before the suffix letter changes identity.** `E150 d` is read
   as `E150`, a different and less specific identifier.
3. **Exact equality between suffixed and unsuffixed numbers.** The
   catalog's `E960` predates Reg. (EU) 2021/1156, which renamed it
   "steviol glycosides from Stevia (**E 960a**)" and added E 960c
   (section B.2). A label printing `E960a` resolves to `E960A` and does
   not match the catalog's `E960`. This is the same mechanism that would
   separate `E150` from `E150d`.
4. **Display name comes from the first OCR token, and tokenization can
   garble it.** Later scans converge on the first persisted row with the
   same E-number (official-identifier precedence,
   `ingredient_catalog.py:857`). Identity convergence is intended, but
   the displayed name is whatever the first scan produced. Here that was
   `"Colour (E150d"`, whose unbalanced parenthesis comes from
   `_NON_WORD_EDGES` stripping the closing `)` in
   `normalize_and_extract_tokens`.

Proposed (not implemented; needs approval because 1–3 change
identity-matching behavior):
- Fold Cyrillic `Е`/`е` to Latin before E-number matching.
- Join a single suffix letter separated by a space only when the result
  is a known suffixed code.
- Map superseded numbers (`E960` ↔ `E960a`) through an explicit,
  source-cited alias table, never by prefix matching. `E150` must not
  match `E150d`, because class IV is a specific additive.
- Fix the parenthesis artifact in tokenization.
- Add an E150d catalog row only through the approved phase-2 content
  process (sample in B.4.6).

## Part B: primary-source verification of the phase-1 proposal

### B.1 Access notes (what could and could not be read)

- **Read in full or at the operative section:**
  - EU Official Journal texts through the Publications Office cellar
    (`publications.europa.eu/resource/celex/<CELEX>.ENG.xhtml` → `DOC_1`).
    `eur-lex.europa.eu` itself returns an HTTP 202 bot challenge with an
    empty body.
  - EFSA Journal articles on PMC.
  - EFSA press pages.
  - DOAJ abstracts.
  - JECFA specification PDFs on `fao.org/fileadmin`.
  - Codex CXG 36 (PDF).
- **Blocked (403 / JavaScript challenge):** EFSA Journal pages on
  `efsa.europa.eu/en/efsajournal/*` and its PDFs, Wiley, GSFA Online, and
  the WHO JECFA database, which renders its results client-side.
- Regulation texts were read in their **original OJ versions**. Later
  consolidated amendments were not checked, except for E 960 (B.2).

### B.2 Status of every phase-1 claim that was not "read"

| Additive | Claim (phase-1 status) | Now | Source actually read |
|---|---|---|---|
| E322 | Mixtures/fractions of phosphatides from animal or vegetable foodstuffs (search-snippet) | **read** | Reg. (EU) No 231/2012 (OJ L 83, 22.3.2012), E 322 definition: "Lecithins are mixtures or fractions of phosphatides obtained by physical procedures from animal or vegetable foodstuffs; they also include hydrolysed products…" |
| E322 | No need for numerical ADI; no concern >1 year; infants 12 wk–11 mo no concern (search-snippet) | **read** | EFSA Journal 2017;15(4):4742, abstract (PMC7010002) |
| E322 | 2020 follow-up conclusions (not read) | **read** | EFSA Journal 2020;18(11):6266, abstract (PMC7654424): lecithins in infant formula (FC 13.1.1) or FSMP (FC 13.1.5.1) for infants <16 weeks "does not raise safety concerns up to the maximum permitted level" |
| E322 | Function "emulsifier" (catalog-only) | **read** | JECFA specification, 41st meeting (1993), FNP 52 Add 2: "FUNCTIONAL USES Emulsifier, antioxidant"; Codex CXG 36 (INS 322(i)): antioxidant, emulsifier |
| E322 | *new:* allergen labelling | **read** | Reg. (EU) No 1169/2011 Annex II items 3 and 6 (eggs; soybeans and products thereof); EFSA 2017 (PMC7010002): "Soybeans and eggs and products thereof (including lecithins) are listed in the Annex II of the Regulation 1169/2011…" |
| E410 | Function "thickener / stabiliser" (catalog-only) | **read** | Codex CXG 36 (INS 410): emulsifier, stabilizer, thickener |
| E410 | *new:* EU definition | **read** | Reg. 231/2012, E 410: "the ground endosperm of the seeds of the strains of carob tree, *Cerationia siliqua* (L.) Taub." (sic: the OJ text spells *Cerationia*; EFSA uses *Ceratonia*); "galactomannan" |
| E951 | Broken down to phenylalanine, aspartic acid, methanol (search-snippet) | **read** | EFSA news, 10 Dec 2013, "EFSA completes full risk assessment on aspartame…": "the breakdown products of aspartame (phenylalanine, methanol and aspartic acid) are also naturally present in other foods"; ADI 40 mg/kg bw/day protective for the general population; not applicable to PKU |
| E951 | Function "sweetener" (catalog-only) | **read** | Codex CXG 36 (INS 951): flavour enhancer, sweetener |
| E220 | 2016 temporary group ADI 0.7 mg SO2 eq/kg bw (search-snippet) | **read** | EFSA Journal 2022;20(11):7594 abstract (PMC9685353): "re-evaluated in 2016, resulting in the setting of a temporary ADI of 0.7 mg SO2 equivalents/kg bw per day" |
| E220 | Sulfite sensitivity incl. asthma (catalog-only, **withheld**) | **read** | same opinion: reactions reported in humans to sulfites used as food additives, "respiratory manifestations are most often observed, in particular in asthmatic individuals" |
| E220 | Labelling threshold 10 mg/kg (not read) | **read** | Reg. (EU) No 1169/2011 Annex II item 12: "Sulphur dioxide and sulphites at concentrations of more than 10 mg/kg or 10 mg/litre in terms of the total SO2…" |
| E220 | Functions / "gas" (catalog-only) | **read** | Reg. 231/2012, E 220: "Colourless, non-flammable gas with strong pungent suffocating odour"; Codex CXG 36 (INS 220): antioxidant, bleaching agent, flour treatment agent, preservative |
| E220 | JECFA's own current SO2 ADI | **not read** | WHO JECFA database blocked (client-side rendering). Owner decision D5 |
| E171 | Withdrawal by Reg. (EU) 2022/63 (search-snippet) | **read** | Reg. (EU) 2022/63 of 14 Jan 2022 (OJ L 11, 18.1.2022). Art. 2: "Until 7 August 2022, foods produced in accordance with the rules applicable before 7 February 2022 may continue to be placed on the market. After that date, they may remain on the market until their date of minimum durability or 'use by' date." Art. 4: in force on the 20th day after publication. Annex: E 171 "not authorised in the food categories listed in Part D and E", kept in list B1 for medicinal products; Art. 3: review within three years |
| E171 | "White pigment", function colour (catalog-only) | **read** | Reg. 231/2012, E 171: synonym "CI Pigment White 6", "pigmentary titanium dioxide"; Codex CXG 36 (INS 171): colour |
| E960 | Numbering subdivided since (unverified lead) | **read** | Reg. (EU) 2021/1156 of 13 Jul 2021 (OJ L 249, 14.7.2021): "steviol glycosides (E 960)" renamed "steviol glycosides from Stevia (E 960a)"; new "E 960c enzymatically produced steviol glycosides"; 18-month labelling transition (Art. 3) |
| E150d | *new* (owner's case): identity/manufacture | **read** | Reg. 231/2012, E 150d: "prepared by the controlled heat treatment of carbohydrates (…glucose syrups, sucrose, and/or invert syrups, and dextrose) with or without acids or alkalis in the presence of both sulphite and ammonium compounds…"; "Dark brown to black liquids or solids"; JECFA caramel colours spec (55th, 2000, FNP 52 Add 8): "Class IV: Sulfite ammonia caramel, INS No. 150d" |
| E150d | *new:* function | **read** | JECFA spec "FUNCTIONAL USES Colour"; Codex CXG 36 (INS 150d): colour |
| E150d | *new:* EFSA group ADI / exposure | **read** (2012) / snippet (2011) | EFSA Journal 2012;10(12):3030 abstract (DOAJ): refined exposure to E 150a/c/d "considerably lower"; "the group ADI of 300 mg/kg bw/day is not exceeded for any population group". The 2011 opinion (EFSA Journal 2011;9(3):2004) that set it: blocked, search-snippet only |
| E150d | *new:* JECFA ADI class IV | **read, dated** | JECFA spec header: "an ADI for Class IV of 0-200 mg/kg bw (0-150 mg/kg bw on solids basis) was established at the 29th JECFA (1985)". Current database entry not checked |

**Codex CXG 36 caveat:** the version read is CAC/GL 36-1989, "Adopted
in 1989. Revision: 2008. Amendment: 2015". Its INS sections "are
regularly updated", so the function classes above are as of that
version.

Unchanged from phase 1 and still "read": E951 EFSA 2013 conclusions and
WHO/IARC/JECFA 2023; E410 EFSA 2017/2023; E220 EFSA 2022 withdrawal; E171
EFSA 2021.

### B.3 Rules applied to the revised samples

- **Content rules.** Every sentence is backed by a **read** source.
  Search-snippet material is left out. Only function classes that both
  JECFA or CXG 36 **and** the product-relevant sense support are named,
  and function lists are not ranked ("mainly") unless a source ranks
  them. Text follows the owner's 2026-09-23 decision: no fixed length,
  and origin, function, effects and jurisdiction kept distinct. That is
  why each sample is split into labelled sections, which is also the
  proposed contract (B.5).
- **No invented content.** Summaries describe the E-number generically,
  never the scanned product. There is no invented safe amount, risk
  score or "harmful" framing, and every limit is attributed with its
  year.

### B.4 Revised EN/BG samples (EN verified; BG = DRAFT machine translation)

#### B.4.1 E322 Lecithins (generic family, never "soy lecithin")

| Section | EN | BG (DRAFT, MACHINE_TRANSLATED) |
|---|---|---|
| ORIGIN | Lecithins (E 322) are mixtures or fractions of phosphatides (phospholipids) obtained by physical procedures from animal or vegetable foods; the EU specification also covers hydrolysed lecithins. The raw material differs between products and is not identified by the E-number alone. | Лецитините (E 322) са смеси или фракции от фосфатиди (фосфолипиди), получени чрез физични процеси от животински или растителни храни; спецификацията на ЕС обхваща и хидролизирани лецитини. Суровината се различава между продуктите и не се определя само от E-номера. |
| FUNCTION | They are used as emulsifiers, helping fat- and water-based ingredients stay mixed, and as antioxidants. | Използват се като емулгатори – помагат мазните и водните съставки да останат смесени – и като антиоксиданти. |
| EFFECTS | EFSA (2017) found no need for a numerical acceptable daily intake and no safety concern for the general population over one year of age at the reported uses. A 2020 EFSA follow-up found no safety concern for use in infant formula and foods for special medical purposes for infants under 16 weeks, up to the maximum permitted level. | EFSA (2017) не вижда нужда от числено определен допустим дневен прием и не установява риск за безопасността за общото население над едногодишна възраст при отчетените употреби. Последващо становище на EFSA от 2020 г. не установява риск при употреба в храни за кърмачета и храни за специални медицински цели за кърмачета под 16 седмици, до максимално разрешеното ниво. |
| JURISDICTION | EU food-information rules list soybeans and eggs and products made from them, lecithins included, among the allergens that must be indicated. | Правилата на ЕС за информация за храните включват соята и яйцата и продуктите от тях, включително лецитините, сред алергените, които задължително се посочват. |

Citations: Reg. (EU) 231/2012 (E 322); JECFA spec 1993 / CXG 36;
EFSA J 2017;15(4):4742; EFSA J 2020;18(11):6266; Reg. (EU) 1169/2011
Annex II. The soy-specific catalog row keeps its own soy-specific
fields.

#### B.4.2 E410 Locust bean gum (sparse row; the CSV supports none of this)

| Section | EN | BG (DRAFT) |
|---|---|---|
| ORIGIN | Locust bean gum (E 410), also called carob bean gum, is the ground endosperm of carob tree seeds (*Ceratonia siliqua*); it consists mainly of a polysaccharide called galactomannan. | Гумата от рожков (E 410) е смленият ендосперм на семената на рожковото дърво (*Ceratonia siliqua*); състои се главно от полизахарид, наречен галактоманан. |
| FUNCTION | It is used as a thickener and stabiliser and can also act as an emulsifier. | Използва се като сгъстител и стабилизатор и може да действа и като емулгатор. |
| EFFECTS | EFSA (2017) concluded that no numerical acceptable daily intake is needed and found no safety concern for the general population; a 2023 EFSA follow-up assessed its use in foods for infants under 16 weeks and for young children with special medical needs. | EFSA (2017) заключава, че не е необходим числено определен допустим дневен прием, и не установява риск за безопасността за общото население; последващо становище на EFSA от 2023 г. оценява употребата ѝ в храни за кърмачета под 16 седмици и за малки деца със специални медицински нужди. |

Citations: Reg. (EU) 231/2012 (E 410); CXG 36; EFSA J 2017;15(1):4646;
EFSA J 2023;21(2):7775. The 2023 conclusion is stated only as "assessed"
because its conclusion was read as margins of exposure, not as a
yes/no verdict.

#### B.4.3 E951 Aspartame (well described; dose and a sensitive group)

| Section | EN | BG (DRAFT) |
|---|---|---|
| ORIGIN | Aspartame (E 951) is a high-intensity sweetener. In the body it is broken down into phenylalanine, aspartic acid and methanol, which are also naturally present in other foods. | Аспартамът (E 951) е високоинтензивен подсладител. В организма се разгражда до фенилаланин, аспарагинова киселина и метанол, които се съдържат естествено и в други храни. |
| FUNCTION | It is used to sweeten foods and drinks and can also act as a flavour enhancer. | Използва се за подслаждане на храни и напитки и може да действа и като овкусител. |
| EFFECTS | EFSA (2013) concluded that the acceptable daily intake of 40 mg per kg of body weight protects the general population and that exposure estimated at the time was well below it; JECFA (2023) kept the same figure. IARC (2023) classified aspartame as "possibly carcinogenic to humans" (Group 2B) on limited evidence. The acceptable daily intake does not apply to people with phenylketonuria (PKU), who must follow a diet low in phenylalanine. | EFSA (2013) заключава, че допустимият дневен прием от 40 mg на kg телесно тегло защитава общото население и че оцененият тогава прием е далеч под него; JECFA (2023) запазва същата стойност. IARC (2023) класифицира аспартама като „възможно канцерогенен за хората“ (група 2B) при ограничени доказателства. Допустимият дневен прием не се отнася за хора с фенилкетонурия (ФКУ), които трябва да спазват диета с ниско съдържание на фенилаланин. |

Citations: EFSA news 10 Dec 2013 (EFSA J 2013;11(12):3496); WHO/IARC/JECFA
news 14 Jul 2023; CXG 36. "Artificial" was dropped: it was catalog-only.

#### B.4.4 E220 Sulfur dioxide (outdated ADI not copied; the withheld clause is now supported)

| Section | EN | BG (DRAFT) |
|---|---|---|
| ORIGIN | Sulfur dioxide (E 220) is a colourless gas with a pungent odour. EFSA assesses it together with the related sulfites E 221–E 224 and E 226–E 228 as one group. | Серният диоксид (E 220) е безцветен газ с остра миризма. EFSA го оценява заедно със сродните сулфити E 221–E 224 и E 226–E 228 като една група. |
| FUNCTION | It is used as a preservative and antioxidant, and also as a bleaching and flour-treatment agent. | Използва се като консервант и антиоксидант, както и като избелващо средство и средство за обработка на брашно. |
| EFFECTS | In 2022 EFSA withdrew the temporary group acceptable daily intake of 0.7 mg SO2 equivalents per kg of body weight set in 2016, because the toxicity data were inadequate, and concluded that estimated dietary exposures raise a safety concern. Among reported reactions to sulfites used as food additives, breathing-related symptoms are the most common, particularly in people with asthma. | През 2022 г. EFSA оттегли временния групов допустим дневен прием от 0,7 mg SO2 еквиваленти на kg телесно тегло, определен през 2016 г., защото данните за токсичност са недостатъчни, и заключи, че оцененият прием с храната поражда опасения за безопасността. Сред докладваните реакции към сулфити, използвани като хранителни добавки, най-чести са дихателните симптоми, особено при хора с астма. |
| JURISDICTION | In the EU, sulfur dioxide and sulfites above 10 mg/kg or 10 mg/litre (as total SO2) must be indicated on the label as substances causing allergies or intolerances. | В ЕС серният диоксид и сулфитите над 10 mg/kg или 10 mg/литър (като общ SO2) трябва да се посочват на етикета като вещества, предизвикващи алергии или непоносимост. |

Citations: Reg. (EU) 231/2012 (E 220); CXG 36; EFSA J 2022;20(11):7594;
Reg. (EU) 1169/2011 Annex II item 12. The CSV's "0-0.7 (group, JECFA)"
is still not copied (decision D5).

#### B.4.5 E171 Titanium dioxide (jurisdiction- and date-scoped)

| Section | EN | BG (DRAFT) |
|---|---|---|
| ORIGIN | Titanium dioxide (E 171) is a white pigment consisting essentially of titanium dioxide in its anatase and/or rutile forms. | Титановият диоксид (E 171) е бял пигмент, състоящ се основно от титанов диоксид във формите анатаз и/или рутил. |
| FUNCTION | It is used as a colour. | Използва се като оцветител. |
| EFFECTS | In 2021 EFSA concluded that a concern for genotoxicity of titanium dioxide particles could not be ruled out, so E 171 could no longer be considered safe as a food additive; no acceptable daily intake was established. | През 2021 г. EFSA заключи, че не може да се изключи опасение за генотоксичност на частиците титанов диоксид, поради което E 171 вече не може да се счита за безопасен като хранителна добавка; не е определен допустим дневен прием. |
| JURISDICTION | EU only: not authorised in foods under Regulation (EU) 2022/63 (in force 7 February 2022; foods made under the earlier rules could be placed on the market until 7 August 2022 and then remain until their date of minimum durability or use-by date). It remains listed only because of its use as a colour in medicinal products. | Само за ЕС: не е разрешен в храни съгласно Регламент (ЕС) 2022/63 (в сила от 7 февруари 2022 г.; храните, произведени по предишните правила, можеха да се пускат на пазара до 7 август 2022 г. и след това да останат до изтичане на срока им на минимална трайност или „използвай преди“). Остава в списъка само заради употребата си като оцветител в лекарствени продукти. |

Citations: Reg. (EU) 231/2012 (E 171); CXG 36; EFSA J 2021;19(5):6585;
Reg. (EU) 2022/63. "Whiter and more opaque" was dropped: it was
catalog-only.

#### B.4.6 E150d Sulphite ammonia caramel (the owner's case; new sample)

| Section | EN | BG (DRAFT) |
|---|---|---|
| ORIGIN | Sulphite ammonia caramel (E 150d), caramel colour class IV, is made by controlled heating of carbohydrates such as glucose syrups, sucrose, invert syrups or dextrose in the presence of both sulphite and ammonium compounds. It is a dark brown to black liquid or solid. | Сулфитно-амонячният карамел (E 150d), карамелен оцветител клас IV, се получава чрез контролирано нагряване на въглехидрати като глюкозни сиропи, захароза, инвертни сиропи или декстроза в присъствието както на сулфитни, така и на амониеви съединения. Представлява тъмнокафява до черна течност или твърдо вещество. |
| FUNCTION | It is used as a colour. | Използва се като оцветител. |
| EFFECTS | EFSA's group acceptable daily intake for the four caramel colours (E 150a–d) is 300 mg per kg of body weight per day; its 2012 refined exposure assessment found that combined intake did not exceed this group limit for any population group. | Груповият допустим дневен прием на EFSA за четирите карамелени оцветителя (E 150a–d) е 300 mg на kg телесно тегло на ден; уточнената оценка на приема от 2012 г. установи, че общият прием не надвишава тази групова граница за нито една група от населението. |

Citations: Reg. (EU) 231/2012 (E 150d); JECFA caramel colours spec 2000;
CXG 36; EFSA J 2012;10(12):3030. The JECFA class IV ADI (0–200 mg/kg
bw, 1985) is deliberately **not** in the text (decision D5, same
question as E220). Adding this row to the catalog is phase-2 work.

### B.5 Revised contract proposal (supersedes phase-1 section 4.2 where noted)

Changes from the phase-1 contract, driven by the owner's 2026-09-23
comment (no length limit, sections kept distinct):

```jsonc
"summary": {                                  // null/absent when nothing is SOURCE_VERIFIED
  "scope": "E_NUMBER_GENERIC",
  "evidenceState": "SOURCE_VERIFIED",
  "reviewedAt": 1790000000000,
  "sections": [                               // ordered; a section with no verified text is ABSENT, never filler
    { "kind": "ORIGIN",       "text": "…", "citationIds": ["eu-231-2012-e322"] },
    { "kind": "FUNCTION",     "text": "…", "citationIds": ["jecfa-1993-lecithin", "codex-cxg36"] },
    { "kind": "EFFECTS",      "text": "…", "citationIds": ["efsa-2017-4742", "efsa-2020-6266"] },
    { "kind": "JURISDICTION", "text": "…", "citationIds": ["eu-1169-2011-annex-ii"], "jurisdiction": "EU" }
  ],
  "citations": [ { "id": "efsa-2017-4742", "label": "EFSA Journal 2017;15(4):4742",
                   "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7010002/",
                   "documentDate": "2017-04", "supports": ["EFFECTS"] } ]
},
"localizations": { "bg": { "summary": { "sections": [ { "kind": "ORIGIN", "text": "…" } ] } } }
```

- `sections[].kind` is closed: `ORIGIN | FUNCTION | EFFECTS |
  JURISDICTION`. Clients ignore unknown kinds.
- `jurisdiction` is set on `JURISDICTION` sections only (`"EU"` today).
- Citations stay language-independent and top-level. BG sections repeat
  `kind` and never carry their own citations.
- Everything else is unchanged from phase 1: a separate
  `ingredient_summaries` table keyed by E-number; EN served only when
  `SOURCE_VERIFIED`; BG only when `REVIEWED` and hash-current; colour
  never follows the summary; stored, not generated per scan.

### B.6 Remaining explicit decisions (needed before bulk implementation)

| # | Decision | Recommendation |
|---|---|---|
| D1 | Approve the **sectioned** summary contract (B.5) instead of one `text` string | Approve: it enforces the owner's origin/function/effects/jurisdiction separation and lets Android hide empty sections |
| D2 | Summary keyed by E-number in a new table (phase-1 Q1), incl. E322 generic text on the soy row | Approve (unchanged) |
| D3 | Serving rule: EN only `SOURCE_VERIFIED`; BG only `REVIEWED` + hash-current → **BG absent for all codes** until a named Bulgarian reviewer signs off. Who reviews? | Approve rule; owner names a reviewer |
| D4 | What counts as "read" for `SOURCE_VERIFIED`: accept PMC copies, EFSA press pages, DOAJ abstracts and Publications-Office OJ texts as primary (they are official/verbatim copies)? Abstract-only support accepted for conclusions? | Accept PMC/OJ/press as primary; accept abstracts for stated conclusions only |
| D5 | Multiple authorities' numbers (E220 JECFA SO2 ADI, E150d JECFA class IV ADI): show EFSA only, or EFSA + JECFA each attributed? | EFSA only in text for EU users; JECFA figures only after the current JECFA entry is read |
| D6 | Add E150d (and other codes found in real scans) to the phase-2 target set beyond the 45 codes? | Yes for E150d (owner-observed); others by scan evidence, not the placeholder registry |
| D7 | Matching fixes in A.3 (Cyrillic Е, spaced suffix, E960↔E960a alias, name artifact): separate backend issue/branch? | Yes, separate from content work; they change identity matching |
| D8 | Existing catalog over-statements (phase-1 §2.1: E171, E951, E320 rows) corrected in phase 2? | Owner call (unchanged) |

Nothing in B.4 is approved scientific content. It is a verified draft
presented for approval.

### B.7 Limits stated plainly

- **Regulation versions:** read in their original OJ versions.
  Consolidated amendments were checked only for E 960.
- **Blocked sources:** GSFA Online, the EFSA Journal site, Wiley and the
  WHO JECFA database were inaccessible. JECFA figures are as printed in
  the specification headers read.
- **BG text:** all BG is DRAFT machine translation by Claude, with
  identifiers, numbers and years kept identical. It confers neither
  scientific verification nor REVIEWED status.
- **Local sources only:** all sources were fetched into a local
  scratchpad for reading. Nothing was stored in the repository.
