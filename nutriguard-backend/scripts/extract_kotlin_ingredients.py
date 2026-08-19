"""
One-off developer utility: parses the ORIGINAL Android client's
`InitialScientificData.kt` `IngredientEntity(...)` blocks into a JSON
seed file, so the backend's scientific ingredient database starts out
byte-for-byte identical to what ships in the app today.

Not part of the running application; used once to produce
`app/seed/ingredients_seed.json`.
"""
import json
import re
import sys

DEFAULTS = {
    "eNumber": None,
    "whoIarcClassification": None,
    "isGluten": False,
    "isLactose": False,
    "isVegan": True,
    "isVegetarian": True,
    "isHalal": True,
    "isKosher": True,
    "badForDiabetes": False,
    "badForHypertension": False,
    "badForKidneyDisease": False,
    "badForGout": False,
    "badForPregnancy": False,
    "badForChildren": False,
    "badForHighCholesterol": False,
}

FIELD_MAP = {
    "id": "id",
    "commonName": "commonName",
    "scientificName": "scientificName",
    "eNumber": "eNumber",
    "category": "category",
    "description": "description",
    "purposeInFood": "purposeInFood",
    "healthConcerns": "healthConcerns",
    "evidenceLevel": "evidenceLevel",
    "countriesRestrictedOrBanned": "countriesRestrictedOrBanned",
    "efsaStatus": "efsaStatus",
    "fdaStatus": "fdaStatus",
    "whoIarcClassification": "whoIarcClassification",
    "acceptableDailyIntake": "acceptableDailyIntake",
    "sideEffects": "sideEffects",
    "allergens": "allergens",
    "references": "references",
    "riskLevel": "riskLevel",
    "isGluten": "isGluten",
    "isLactose": "isLactose",
    "isVegan": "isVegan",
    "isVegetarian": "isVegetarian",
    "isHalal": "isHalal",
    "isKosher": "isKosher",
    "badForDiabetes": "badForDiabetes",
    "badForHypertension": "badForHypertension",
    "badForKidneyDisease": "badForKidneyDisease",
    "badForGout": "badForGout",
    "badForPregnancy": "badForPregnancy",
    "badForChildren": "badForChildren",
    "badForHighCholesterol": "badForHighCholesterol",
}


def split_top_level_args(body: str) -> list[str]:
    args = []
    depth = 0
    current = []
    in_string = False
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == '"' and (i == 0 or body[i - 1] != "\\"):
            in_string = not in_string
            current.append(ch)
        elif ch == "(" and not in_string:
            depth += 1
            current.append(ch)
        elif ch == ")" and not in_string:
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0 and not in_string:
            args.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if current:
        args.append("".join(current).strip())
    return [a for a in args if a]


def parse_value(raw: str):
    raw = raw.strip()
    if raw == "null":
        return None
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('\\"', '"')
    m = re.match(r"RiskLevel\.(\w+)", raw)
    if m:
        return m.group(1)
    return raw


def main(src_path: str, out_path: str) -> None:
    text = open(src_path, encoding="utf-8").read()

    # Find each `IngredientEntity(` ... matching close paren block.
    results = []
    idx = 0
    while True:
        start = text.find("IngredientEntity(", idx)
        if start == -1:
            break
        open_paren = start + len("IngredientEntity(") - 1
        depth = 0
        i = open_paren
        in_string = False
        while i < len(text):
            ch = text[i]
            if ch == '"' and text[i - 1] != "\\":
                in_string = not in_string
            elif ch == "(" and not in_string:
                depth += 1
            elif ch == ")" and not in_string:
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = text[open_paren + 1 : i]
        idx = i + 1

        fields = dict(DEFAULTS)
        for arg in split_top_level_args(body):
            if "=" not in arg:
                continue
            key, _, val = arg.partition("=")
            key = key.strip()
            if key in FIELD_MAP:
                fields[FIELD_MAP[key]] = parse_value(val)
        results.append(fields)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Extracted {len(results)} ingredients -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
