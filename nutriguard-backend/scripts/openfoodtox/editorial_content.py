"""Curated, source-cited editorial content for the four pilot substance
review profiles (docs/OPENFOODTOX_PROFILE_CONTENT_TASK.md).

Separately versioned from the raw OpenFoodTox extraction (see
``records.EXTRACTION_LOGIC_VERSION``) because this is authored editorial
prose -- identity/origin, purpose-in-food, and effects narrative -- never
mechanically derived from a structured field, and never produced by a
live model call at runtime (this module is fixed, reviewed text checked
into the repository, exactly like ``app/seed/e_additives_curated_starter.csv``,
which it partly draws on).

Every piece of text carries its own ``source_kind`` so it is never
confused with OpenFoodTox's own extracted/quoted evidence:

- ``"openfoodtox_dossier"`` -- restates/frames a specific OpenFoodTox
  extracted field or its own internal-evidence quote.
- ``"tracked_seed_csv"`` -- NutriGuard's own already-curated, git-tracked
  seed data (``app/seed/e_additives_curated_starter.csv`` /
  ``ingredients_seed.json``), itself cited to EFSA/JECFA/Codex/EU topic
  pages, last reviewed 2026-09-08.
- ``"external_primary_source"`` -- a primary source (an EFSA opinion,
  press release, plain-language summary, or EU regulation) read directly
  in this session, not a search-result snippet. URL and access date are
  recorded with every citation.

``EditorialEntry.editorial_chemical_basis`` is the one place this module
can affect numeric-guidance eligibility (see
``evidence_bundle.py::_assess_review_eligibility``'s ``editorial_basis``
parameter): it is set *only* when this session directly confirmed, by
reading primary source text itself, that a reference value's chemical
basis is unambiguous -- never a guess, and always carrying its own
citation. It is deliberately left unset for E150d (see that entry's
``group_scope_note`` for why) -- an uncertain basis is retained as
unresolved rather than guessed, per the task's own instruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourcedText:
    en: str
    bg: str
    source_kind: str  # "openfoodtox_dossier" | "tracked_seed_csv" | "external_primary_source"
    source: str

    def to_dict(self) -> dict:
        return {"en": self.en, "bg": self.bg, "source_kind": self.source_kind, "source": self.source}


@dataclass(frozen=True)
class EffectNote:
    evidence_type: str  # "human" | "animal_in_vitro" | "assessment_conclusion"
    text: SourcedText

    def to_dict(self) -> dict:
        return {"evidence_type": self.evidence_type, **self.text.to_dict()}


@dataclass(frozen=True)
class EditorialEntry:
    e_number: str
    identity: SourcedText | None = None
    purpose: SourcedText | None = None
    effects: list[EffectNote] = field(default_factory=list)
    population_exceptions: list[SourcedText] = field(default_factory=list)
    group_scope_note: SourcedText | None = None
    editorial_chemical_basis: dict | None = None  # {"basis_en","basis_bg","source"}
    external_sources: list[dict] = field(default_factory=list)  # [{"title","url","access_date","note"}]
    operator_only_notes: list[SourcedText] = field(default_factory=list)  # never shown in consumer draft


EDITORIAL_CONTENT_VERSION = 1

_REG_231_2012 = (
    "Commission Regulation (EU) No 231/2012 of 9 March 2012 laying down specifications for "
    "food additives listed in Annexes II and III to Regulation (EC) No 1333/2008 "
    "(https://eur-lex.europa.eu/eli/reg/2012/231/oj); read 2026-10-01."
)

EDITORIAL_CONTENT: dict[str, EditorialEntry] = {
    "E250": EditorialEntry(
        e_number="E250",
        identity=SourcedText(
            en=(
                "Sodium nitrite is an inorganic salt of nitrous acid (NaNO2), produced industrially; "
                "it is not typically present in foods naturally at significant levels."
            ),
            bg=(
                "Натриевият нитрит е неорганична сол на азотистата киселина (NaNO2), произвеждана по "
                "промишлен начин; обикновено не се среща естествено в храните в значими количества."
            ),
            source_kind="external_primary_source",
            source=_REG_231_2012,
        ),
        purpose=SourcedText(
            en=(
                "Used as a curing agent and preservative in meat products: it inhibits the germination "
                "and toxin production of Clostridium botulinum spores and contributes to the "
                "characteristic colour and flavour of cured meats."
            ),
            bg=(
                "Използва се като агент за осоляване и консервант в месни продукти: инхибира "
                "покълването на спорите на Clostridium botulinum и образуването на техния токсин, и "
                "допринася за характерния цвят и вкус на осолените месни продукти."
            ),
            source_kind="tracked_seed_csv",
            source=(
                "app/seed/e_additives_curated_starter.csv, row E250 (functional_class/"
                "typical_role_or_foods), curated starter, last_reviewed 2026-09-08"
            ),
        ),
        effects=[
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "EFSA's 2017 re-evaluation identified an increase in blood methaemoglobin level "
                        "as the relevant effect for deriving the ADI, using a benchmark-dose approach."
                    ),
                    bg=(
                        "Преоценката на ЕФСА от 2017 г. определя повишаването на нивото на метхемоглобин "
                        "в кръвта като значимия ефект за извеждане на ADI, въз основа на подход с "
                        "референтна доза (benchmark dose)."
                    ),
                    source_kind="openfoodtox_dossier",
                    source="OpenFoodTox dossier, doi:10.2903/j.efsa.2017.4786, internal evidence quote",
                ),
            ),
            EffectNote(
                "human",
                SourcedText(
                    en=(
                        "Epidemiological studies linking processed/cured meat consumption to health "
                        "outcomes are not specific to nitrite alone and involve multiple co-exposures "
                        "from other components of processed meat, so they cannot be read as evidence "
                        "about sodium nitrite in isolation."
                    ),
                    bg=(
                        "Епидемиологичните проучвания, свързващи консумацията на преработено/осолено "
                        "месо със здравни резултати, не са специфични само за нитрита и включват "
                        "множество съпътстващи експозиции от други съставки на преработеното месо, "
                        "поради което не могат да се тълкуват като доказателство единствено за "
                        "натриевия нитрит."
                    ),
                    source_kind="tracked_seed_csv",
                    source="app/seed/e_additives_curated_starter.csv, row E250 (human_evidence)",
                ),
            ),
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "The Panel also noted that nitrite can participate in the formation of "
                        "endogenous N-nitroso compounds under some conditions; this consideration fed "
                        "into the overall risk characterisation rather than into the ADI figure itself."
                    ),
                    bg=(
                        "Панелът отбелязва също, че нитритът може да участва в образуването на "
                        "ендогенни N-нитрозо съединения при определени условия; това съображение е "
                        "взето предвид в цялостната характеристика на риска, а не самостоятелно в "
                        "стойността на ADI."
                    ),
                    source_kind="tracked_seed_csv",
                    source="app/seed/e_additives_curated_starter.csv, row E250 (potential_effects)",
                ),
            ),
        ],
        external_sources=[
            {
                "title": "Commission Regulation (EU) No 231/2012 (food additive specifications)",
                "url": "https://eur-lex.europa.eu/eli/reg/2012/231/oj",
                "access_date": "2026-10-01",
                "note": "Identity/specification of sodium nitrite.",
            }
        ],
    ),
    "E150d": EditorialEntry(
        e_number="E150d",
        identity=SourcedText(
            en=(
                "Sulphite ammonia caramel (caramel colour IV) is one of four EU-defined caramel colour "
                "classes (E150a-d), made by controlled heating of carbohydrates. E150d is specifically "
                "produced in the presence of both sulphite and ammonium compounds, distinguishing it "
                "from E150a (neither), E150b (sulphite compounds only) and E150c (ammonium compounds "
                "only)."
            ),
            bg=(
                "Сулфитно-амонячният карамел (карамелен цвят клас IV) е един от четирите дефинирани от "
                "ЕС класа карамелени оцветители (E150a-d), получавани чрез контролирано нагряване на "
                "въглехидрати. E150d се произвежда специално в присъствието както на сулфитни, така и "
                "на амониеви съединения, което го отличава от E150a (без тях), E150b (само сулфитни "
                "съединения) и E150c (само амониеви съединения)."
            ),
            source_kind="external_primary_source",
            source=_REG_231_2012,
        ),
        purpose=SourcedText(
            en="Used as a brown food colourant, e.g. in cola-type soft drinks, spirits, sauces and baked goods.",
            bg="Използва се като кафяв хранителен оцветител, напр. в колови безалкохолни напитки, спиртни напитки, сосове и печива.",
            source_kind="external_primary_source",
            source="General food-colour usage, cross-checked against the 2011 EFSA opinion's background section; read 2026-10-01.",
        ),
        effects=[
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "EFSA's 2011 re-evaluation set a GROUP Acceptable Daily Intake that applies to "
                        "the COMBINED exposure to all four caramel colours (E150a, b, c and d) together "
                        "-- it is not an individual allowance for E150d alone. Only E150c (ammonia "
                        "caramel, a different substance from the one in this profile) carries an "
                        "additional, stricter limit of its own within that group total, due to "
                        "uncertainty about a possible immune-system effect observed in animals for one "
                        "of its constituents; E150d itself carries no separate limit beyond the group "
                        "figure. The exact figures are not yet included in this preview."
                    ),
                    bg=(
                        "Преоценката на ЕФСА от 2011 г. определя ГРУПОВА допустима дневна доза (ADI), "
                        "която се прилага за КОМБИНИРАНАТА експозиция на всичките четири карамелени "
                        "оцветителя (E150a, b, c и d) заедно -- това не е индивидуално допустимо "
                        "количество само за E150d. Само E150c (амонячен карамел, различно вещество от "
                        "разглежданото в този профил) носи собствена допълнителна, по-строга граница в "
                        "рамките на тази обща стойност, поради несигурност относно възможен ефект върху "
                        "имунната система, наблюдаван при животни за една от съставките му; E150d не "
                        "носи отделна граница извън груповата стойност. Точните стойности все още не са "
                        "включени в този преглед."
                    ),
                    source_kind="external_primary_source",
                    source=(
                        "EFSA ANS Panel, Scientific Opinion on the re-evaluation of caramel colours "
                        "(E150a,b,c,d) as food additives, doi:10.2903/j.efsa.2011.2004, March 2011; "
                        "independently confirmed via EFSA-sourced secondary reporting, read 2026-10-01."
                    ),
                ),
            ),
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "A 2012 EFSA follow-up refined the exposure estimate using updated use-level "
                        "data: actual consumer exposure to E150a, c and d individually was found to be "
                        "considerably lower than first estimated in 2011. This update concerns exposure "
                        "estimation only -- it did not change the ADI figures themselves."
                    ),
                    bg=(
                        "Последващо проучване на ЕФСА от 2012 г. прецизира оценката на експозицията чрез "
                        "актуализирани данни за нивата на употреба: установено е, че действителната "
                        "експозиция на потребителите на E150a, c и d поотделно е значително по-ниска от "
                        "първоначално оценената през 2011 г. Тази актуализация засяга само оценката на "
                        "експозицията -- не променя самите стойности на ADI."
                    ),
                    source_kind="external_primary_source",
                    source=(
                        "EFSA, \"Refined exposure assessment for caramel colours (E150a, c, d)\", "
                        "doi:10.2903/j.efsa.2012.3030, December 2012; read 2026-10-01."
                    ),
                ),
            ),
        ],
        # group_scope_note is intentionally left unset: its old content
        # (explaining the automated chemical-basis status) was an
        # internal/technical note, not consumer-appropriate text -- moved
        # into operator_only_notes below instead. The consumer-safe scope
        # explanation is already in the first effects note above.
        operator_only_notes=[
            SourcedText(
                en=(
                    "The group ADI is 300 mg/kg bw/day (all four caramel colours combined); E150c's own "
                    "additional sub-limit is 100 mg/kg bw/day. OpenFoodTox's own extracted justification "
                    "text for this record is a short, non-descriptive comment (\"ADI (group)\") with no "
                    "numeric magnitude to validate -- the automated chemical-basis check for this record "
                    "is kept UNRESOLVED rather than guessed at, even though these figures and their scope "
                    "are independently confirmed via the primary 2011 opinion. Per this round's single "
                    "gating policy, an externally-sourced number for an otherwise-ineligible record is "
                    "withheld from the consumer preview exactly like an internally-extracted one -- kept "
                    "here, in the operator bundle, for review."
                ),
                bg=(
                    "Груповата ADI е 300 мг/кг телесно тегло дневно (всички четири карамелени "
                    "оцветителя заедно); собствената допълнителна граница на E150c е 100 мг/кг телесно "
                    "тегло дневно. Собственият извлечен от OpenFoodTox обосноваващ текст за този запис е "
                    "кратък, неописателен коментар (\"ADI (group)\") без числена стойност за проверка -- "
                    "автоматизираната проверка на химичната основа за този запис се запазва като "
                    "НЕУСТАНОВЕНА, вместо да се предполага, въпреки че тези стойности и обхватът им са "
                    "независимо потвърдени чрез основното становище от 2011 г. Съгласно единната политика "
                    "за допустимост на този кръг, външно обоснована стойност за иначе недопустим запис се "
                    "задържа от прегледа за потребители по същия начин като вътрешно извлечена -- "
                    "запазена тук, в пакета за оператора, за преглед."
                ),
                source_kind="openfoodtox_dossier",
                source="OpenFoodTox dossier, doi:10.2903/j.efsa.2011.2004, internal evidence quote",
            )
        ],
        external_sources=[
            {
                "title": "Commission Regulation (EU) No 231/2012 (caramel colour specifications)",
                "url": "https://eur-lex.europa.eu/eli/reg/2012/231/oj",
                "access_date": "2026-10-01",
            },
            {
                "title": "EFSA ANS Panel, re-evaluation of caramel colours (E150a,b,c,d) as food additives",
                "url": "https://doi.org/10.2903/j.efsa.2011.2004",
                "access_date": "2026-10-01",
            },
            {
                "title": "EFSA, Refined exposure assessment for caramel colours (E150a, c, d)",
                "url": "https://doi.org/10.2903/j.efsa.2012.3030",
                "access_date": "2026-10-01",
            },
        ],
    ),
    "E330": EditorialEntry(
        e_number="E330",
        identity=SourcedText(
            en=(
                "Citric acid occurs naturally in citrus fruits. Today it is produced industrially at "
                "large scale by fermentation of carbohydrates using moulds such as Aspergillus niger, "
                "rather than extracted from fruit."
            ),
            bg=(
                "Лимонената киселина се среща естествено в цитрусовите плодове. Днес се произвежда "
                "промишлено в голям мащаб чрез ферментация на въглехидрати с помощта на плесени като "
                "Aspergillus niger, а не чрез извличане от плодове."
            ),
            source_kind="external_primary_source",
            source=(
                _REG_231_2012
                + " Annex, E 330 CITRIC ACID entry, 'Definition': the Regulation's own definition states "
                "citric acid 'is obtained by fermentation of carbohydrate solutions (e.g., glucose syrups) "
                "with the mould Aspergillus niger' -- the natural-occurrence and industrial-fermentation "
                "claim above restates this definition text directly, not a separate, uncited reference."
            ),
        ),
        purpose=SourcedText(
            en=(
                "Used as an acidity regulator and sequestrant: it adjusts/stabilises pH (acidification) "
                "and binds (chelates) trace metal ions, and supports flavour in many foods and beverages."
            ),
            bg=(
                "Използва се като регулатор на киселинността и секвестрант: коригира/стабилизира pH "
                "(подкиселяване) и свързва (хелатира) йони на следови метали, а също подпомага вкуса в "
                "много храни и напитки."
            ),
            source_kind="tracked_seed_csv",
            source="app/seed/e_additives_curated_starter.csv, row E330 (functional_class/typical_role_or_foods)",
        ),
        effects=[
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "JECFA concluded in 1974 that citric acid and its calcium/potassium/sodium "
                        "salts did not constitute a significant hazard to health, given its "
                        "well-established role as a normal intermediary of metabolism, and set the ADI "
                        "as 'not limited'. This is a specific regulatory conclusion -- that no numeric "
                        "limit was considered necessary based on the available data -- not a statement "
                        "that any amount is automatically safe regardless of quantity or context."
                    ),
                    bg=(
                        "През 1974 г. JECFA заключава, че лимонената киселина и нейните калциеви/"
                        "калиеви/натриеви соли не представляват значителен риск за здравето, предвид "
                        "установената ѝ роля като нормален междинен продукт на метаболизма, и определя "
                        "ADI като „неограничена“. Това е конкретно регулаторно заключение -- че не е "
                        "счетено за необходимо числено ограничение въз основа на наличните данни -- а "
                        "не твърдение, че всяко количество е автоматично безопасно независимо от "
                        "количеството или контекста."
                    ),
                    source_kind="openfoodtox_dossier",
                    source="OpenFoodTox dossier, doi:10.2903/j.efsa.2016.4599, internal evidence quote (citing JECFA 1974)",
                ),
            ),
        ],
        operator_only_notes=[
            SourcedText(
                en=(
                    "Separately, EFSA's FEEDAP Panel has assessed citric acid's use as an animal-feed "
                    "additive (not a human food evaluation); those findings concern animal nutrition "
                    "and worker handling, not human dietary guidance, and are kept out of the consumer "
                    "preview entirely -- see the operator evidence bundle."
                ),
                bg=(
                    "Отделно, панелът FEEDAP на ЕФСА е оценил употребата на лимонена киселина като "
                    "фуражна добавка (не оценка за храна за хора); тези констатации се отнасят до "
                    "храненето на животни и работата с веществото, а не до насоки за хранене на хора, и "
                    "са изцяло изключени от прегледа за потребители -- вижте пакета с оперативни "
                    "доказателства."
                ),
                source_kind="openfoodtox_dossier",
                source="OpenFoodTox FEEDAP dossiers, doi:10.2903/j.efsa.2015.4010 and doi:10.2903/j.efsa.2015.4009",
            )
        ],
        external_sources=[
            {
                "title": "Commission Regulation (EU) No 231/2012 (citric acid specification)",
                "url": "https://eur-lex.europa.eu/eli/reg/2012/231/oj",
                "access_date": "2026-10-01",
            }
        ],
    ),
    "E951": EditorialEntry(
        e_number="E951",
        identity=SourcedText(
            en=(
                "Aspartame is a synthetic dipeptide sweetener (L-aspartyl-L-phenylalanine methyl "
                "ester), roughly 200 times sweeter than sucrose by weight."
            ),
            bg=(
                "Аспартамът е синтетичен дипептиден подсладител (L-аспартил-L-фенилаланинов метилов "
                "естер), приблизително 200 пъти по-сладък от захарозата по тегло."
            ),
            source_kind="tracked_seed_csv",
            source="app/seed/ingredients_seed.json, entry e951_aspartame (scientificName/description)",
        ),
        purpose=SourcedText(
            en="Used as a high-intensity, non-nutritive sweetener in diet beverages, sugar-free confectionery and similar products.",
            bg="Използва се като нискокалоричен подсладител с висока сладост в диетични напитки, беззахарни сладкарски изделия и подобни продукти.",
            source_kind="tracked_seed_csv",
            source="app/seed/ingredients_seed.json, entry e951_aspartame (purposeInFood)",
        ),
        effects=[
            EffectNote(
                "human",
                SourcedText(
                    en=(
                        "Aspartame is broken down in the gut into its constituent parts -- "
                        "phenylalanine, aspartic acid and methanol -- which then enter the body's "
                        "normal metabolic pathways."
                    ),
                    bg=(
                        "В червата аспартамът се разгражда до съставните си части -- фенилаланин, "
                        "аспарагинова киселина и метанол -- които след това навлизат в нормалните "
                        "метаболитни пътища на организма."
                    ),
                    source_kind="tracked_seed_csv",
                    source="app/seed/e_additives_curated_starter.csv, row E951 (digestion_absorption/metabolism)",
                ),
            ),
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "Across five separate EFSA assessments between 2006 and 2013, EFSA consistently "
                        "concluded there was no reason to revise the Acceptable Daily Intake of 40 "
                        "mg/kg body weight per day for aspartame, based on the evidence available at "
                        "each assessment date."
                    ),
                    bg=(
                        "В пет отделни оценки на ЕФСА между 2006 и 2013 г. ЕФСА последователно "
                        "заключава, че няма основание за преразглеждане на допустимата дневна доза от "
                        "40 мг/кг телесно тегло дневно за аспартам, въз основа на наличните към всяка "
                        "дата на оценка данни."
                    ),
                    source_kind="openfoodtox_dossier",
                    source="OpenFoodTox aspartame dossiers, 2006-2013 (five assessments), internal evidence quotes",
                ),
            ),
            EffectNote(
                "assessment_conclusion",
                SourcedText(
                    en=(
                        "In 2023, IARC classified aspartame as 'possibly carcinogenic to humans' "
                        "(Group 2B, based on limited evidence), while JECFA, reviewing the same new "
                        "evidence on the same day, reaffirmed the existing 40 mg/kg bw/day ADI and "
                        "concluded dietary exposure at current levels does not pose a health concern. "
                        "A 2026 EFSA re-evaluation (within the scope of re-assessing E962, see Intake "
                        "information below) likewise found no causal link between current aspartame "
                        "exposure and the health outcomes examined, and did not change the ADI."
                    ),
                    bg=(
                        "През 2023 г. IARC класифицира аспартама като „възможно канцерогенен за хора“ "
                        "(Група 2B, въз основа на ограничени доказателства), докато JECFA, преглеждайки "
                        "същите нови данни в същия ден, потвърждава съществуващата ADI от 40 мг/кг "
                        "телесно тегло дневно и заключава, че хранителната експозиция при настоящите "
                        "нива не поражда опасения за здравето. Преоценка на ЕФСА от 2026 г. (в рамките "
                        "на преоценката на E962, вижте „Информация за прием“ по-долу) също не установява "
                        "причинно-следствена връзка между настоящата експозиция на аспартам и "
                        "изследваните здравни резултати и не променя ADI."
                    ),
                    source_kind="external_primary_source",
                    source=(
                        "IARC/WHO press release, 2023-07-14; EFSA plain-language summary, "
                        "\"Re-evaluation of salt of aspartame-acesulfame (E 962) as food additive\" "
                        "(efsa.europa.eu), read 2026-10-01. Underlying opinion doi:10.2903/j.efsa.2026.10259 "
                        "-- First published: 10 September 2026; Approved: 1 July 2026 (dates kept "
                        "distinct; the opinion's own scope is an updated toxicological and "
                        "dietary-exposure assessment of E951 conducted within the E962 re-evaluation, "
                        "using literature up to June 2025 -- not a standalone full re-evaluation of "
                        "E951 and not a claim that every part of the 2013 opinion was superseded)."
                    ),
                ),
            ),
        ],
        population_exceptions=[
            SourcedText(
                en=(
                    "The ADI of 40 mg/kg bw/day is NOT applicable to people with phenylketonuria (PKU): "
                    "they require total control of dietary phenylalanine intake to manage the risk from "
                    "elevated phenylalanine plasma levels, independent of the ADI set for the general "
                    "population. This is an existing medical dietary restriction for PKU management, "
                    "not personalised guidance newly derived here."
                ),
                bg=(
                    "Допустимата дневна доза (ADI) от 40 мг/кг телесно тегло дневно НЕ се прилага за "
                    "хора с фенилкетонурия (ФКУ): те се нуждаят от пълен контрол на приема на "
                    "фенилаланин с храната, за да управляват риска от повишени плазмени нива на "
                    "фенилаланин, независимо от ADI, определена за общото население. Това е "
                    "съществуващо медицинско хранително ограничение за управление на ФКУ, а не "
                    "персонализирана препоръка, изведена тук наново."
                ),
                source_kind="openfoodtox_dossier",
                source="OpenFoodTox dossier, doi:10.2903/j.efsa.2013.3496, internal evidence quote",
            )
        ],
        editorial_chemical_basis={
            "basis_en": "aspartame itself",
            "basis_bg": "самият аспартам",
            "explanation_en": (
                "The ADI is expressed per kg of aspartame; aspartame has no alternative salt/ion form "
                "analogous to sodium nitrite's salt-vs-ion duality. The automated per-record "
                "chemical-basis check looks for the pattern \"<number> mg <name>/kg bw\" in each "
                "dossier's own justification text, which none of the five aspartame dossiers happens to "
                "use verbatim (they state the ADI as e.g. \"40 mg/kg bw per day\" without repeating "
                "\"aspartame\" immediately after \"mg\") -- so the automated field correctly reports "
                "unresolved_no_mention for each record. This editorial confirmation is based on directly "
                "reading all five dossiers' own justification text (none mentions an alternate basis), "
                "not a guess, and is kept explicitly distinct from the automated field."
            ),
            "explanation_bg": (
                "ADI е изразена на kg аспартам; аспартамът няма алтернативна форма на сол/йон, "
                "аналогична на двойствеността сол/йон при натриевия нитрит. Автоматизираната проверка на "
                "химичната основа за всеки отделен запис търси образеца „<число> mg <име>/kg bw“ в "
                "собствения обосноваващ текст на всяко досие, който нито едно от петте досиета за "
                "аспартам не използва буквално -- затова автоматизираното поле коректно отчита "
                "unresolved_no_mention за всеки запис. Това редакционно потвърждение се основава на "
                "пряко прочитане на обосноваващия текст на всичките пет досиета (нито едно не споменава "
                "алтернативна основа), а не на предположение, и се запазва изрично отделно от "
                "автоматизираното поле."
            ),
            "source": "Direct reading of all five OpenFoodTox aspartame dossiers' justification_and_comments text, 2026-10-01.",
        },
        external_sources=[
            {
                "title": "EFSA plain-language summary: Re-evaluation of salt of aspartame-acesulfame (E 962) as food additive",
                "url": "https://www.efsa.europa.eu/en/plain-language-summary/re-evaluation-salt-aspartame-acesulfame-e-962-food-additive",
                "access_date": "2026-10-01",
                "note": (
                    "Underlying opinion doi:10.2903/j.efsa.2026.10259 -- First published: 10 September "
                    "2026; Approved: 1 July 2026. Scope: updated toxicological and dietary-exposure "
                    "assessment of E951 within the E962 re-evaluation (literature to June 2025), not a "
                    "standalone E951 re-evaluation; ADI for E951 stated as remaining valid at 40 mg/kg "
                    "bw/day."
                ),
            },
            {
                "title": "EFSA press release: EFSA completes full risk assessment on aspartame and concludes it is safe at current levels of exposure",
                "url": "https://www.efsa.europa.eu/en/press/news/131210",
                "access_date": "2026-10-01",
            },
        ],
    ),
}


def get_editorial_entry(e_number_normalized: str | None) -> EditorialEntry | None:
    if not e_number_normalized:
        return None
    return EDITORIAL_CONTENT.get(e_number_normalized)
