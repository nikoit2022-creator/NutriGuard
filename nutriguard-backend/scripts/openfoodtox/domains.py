"""Classification of IUCLID ``documentSubType`` values into evidence domains.

This is a **schema-level heuristic**, not a per-record content
verification: it classifies a record by what its ``documentSubType``
declares itself to be (e.g. ``RepeatedDoseToxicityOral`` -> human
health, ``ShortTermToxicityToFish`` -> environmental), using the
standard IUCLID/ECHA section semantics for these subtype names. It
does **not** re-derive the classification from the species/route/
population actually recorded inside each individual study, so it can
misclassify an individual record if its content is atypical for its
declared subtype (for example, a record mistakenly filed under a
human-health subtype but actually describing an environmental study —
we have not observed this in the sampled data, but have not exhaustively
checked all 11k+ archives for it either).

The classification table below was built from a full manifest-level
survey of every ``(documentType, documentSubType)`` pair present in
the transferred dataset (see
``docs/OPENFOODTOX_DATASET_AUDIT.md`` §2 for the raw counts). Any
subtype encountered that is *not* in this table is classified as
``"unclassified"`` rather than guessed into a bucket.
"""

from __future__ import annotations

# domain values:
#   human_health            - endpoints directly bearing on human health risk assessment
#   environmental            - ecotoxicology / wildlife / environmental-fate endpoints
#   livestock_animal_health   - farm-animal (non-wildlife, non-human) health endpoints
#   physicochemical           - substance physical/chemical property endpoints
#   supporting                - composition/use/metabolite/test-material context, not a finding
#   assessment_summary        - dossier/administrative/reference-value container types

DOMAIN_BY_SUBTYPE: dict[str, str] = {
    # --- human health: endpoint summaries & study records ---
    "Carcinogenicity_EU_PPP": "human_health",
    "Carcinogenicity": "human_health",
    "GeneticToxicity": "human_health",
    "GeneticToxicityVitro": "human_health",
    "GeneticToxicityVivo": "human_health",
    "RepeatedDoseToxicityOral": "human_health",
    "RepeatedDoseToxicityOther": "human_health",
    "RepeatedDoseToxicityInhalation": "human_health",
    "RepeatedDoseToxicityDermal": "human_health",
    "AcuteToxicityOral": "human_health",
    "AcuteToxicityOtherRoutes": "human_health",
    "AcuteToxicityInhalation": "human_health",
    "AcuteToxicityDermal": "human_health",
    "ToxicityReproduction": "human_health",
    "ToxicityReproductionOther": "human_health",
    "DevelopmentalToxicityTeratogenicity": "human_health",
    "Neurotoxicity": "human_health",
    "EpidemiologicalData": "human_health",
    "AdditionalToxicologicalInformation": "human_health",
    "Toxicokinetics": "human_health",
    "BasicToxicokinetics": "human_health",
    # --- livestock / farm-animal health (not human, not wildlife) ---
    "ToxicEffectsLivestock": "livestock_animal_health",
    # --- environmental / ecotoxicology / environmental fate ---
    "ToxicityToBirds": "environmental",
    "ToxicityToBees": "environmental",
    "ToxicityToSoilArthropods": "environmental",
    "ToxicityToTerrestrialArthropodsOtherThanBees": "environmental",
    "ToxicityToAquaticAlgae": "environmental",
    "ToxicityToAquaticPlant": "environmental",
    "ToxicityToTerrestrialPlants": "environmental",
    "LongTermToxicityToAquaInv": "environmental",
    "ShortTermToxicityToAquaInv": "environmental",
    "ShortTermToxicityToFish": "environmental",
    "LongTermToxToFish": "environmental",
    "SedimentToxicity": "environmental",
    "AdditionalEcotoxicologicalInformation": "environmental",
    "BiodegradationInSoil": "environmental",
    "BiodegradationInWaterAndSedimentSimulationTests": "environmental",
    "BiodegradationInWaterScreeningTests": "environmental",
    "Phototransformation": "environmental",
    "PhototransformationInAir": "environmental",
    "PhotoTransformationInSoil": "environmental",
    "AdditionalInformationOnEnvironmentalFateAndBehaviour": "environmental",
    "OtherDistributionData": "environmental",
    "AdsorptionDesorption": "environmental",
    "Hydrolysis": "environmental",
    # --- physicochemical properties ---
    "SolubilityOrganic": "physicochemical",
    "AdditionalPhysicoChemical": "physicochemical",
    "WaterSolubility": "physicochemical",
    "BoilingPoint": "physicochemical",
    "Partition": "physicochemical",
    "Vapour": "physicochemical",
    "HenrysLawConstant": "physicochemical",
    "DissociationConstant": "physicochemical",
    "SurfaceTension": "physicochemical",
    "Flammability": "physicochemical",
    "Explosiveness": "physicochemical",
    "OxidisingProperties": "physicochemical",
    "Melting": "physicochemical",
    "FlashPoint": "physicochemical",
    "AutoFlammability": "physicochemical",
    "GeneralInformation": "physicochemical",
    # --- supporting / contextual, not itself a finding ---
    "Metabolites": "supporting",
    "SubstanceComposition": "supporting",
    "ConsumerUses": "supporting",
    # --- assessment / reference-value / administrative ---
    "ToxRefValues": "assessment_summary",
    "EFSA_CHEMICALS_DATABASE": "assessment_summary",
}

# documentType values that are identity/administrative, not findings, and
# are always handled by their own dedicated extractor regardless of subtype.
NON_FINDING_DOCUMENT_TYPES = {"SUBSTANCE", "REFERENCE_SUBSTANCE", "LEGAL_ENTITY", "LITERATURE", "DOSSIER"}


def classify(document_type: str | None, document_sub_type: str | None) -> str:
    if document_type in NON_FINDING_DOCUMENT_TYPES:
        return "assessment_summary" if document_type in ("DOSSIER", "LITERATURE") else "identity"
    if document_sub_type and document_sub_type in DOMAIN_BY_SUBTYPE:
        return DOMAIN_BY_SUBTYPE[document_sub_type]
    if document_type == "TEST_MATERIAL_INFORMATION":
        return "supporting"
    return "unclassified"
