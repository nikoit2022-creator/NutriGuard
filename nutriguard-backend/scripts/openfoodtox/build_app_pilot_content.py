#!/usr/bin/env python3
"""Build the small, tracked, versioned EN/BG content artifact the backend
import path (``app/seed/load_openfoodtox_pilot_content.py``) reads at
runtime for the four owner-approved OpenFoodTox pilot identities (E250,
E150d, E330, E951) -- see
docs/OPENFOODTOX_APP_PILOT_INTEGRATION_TASK.md.

Reads ``scripts/openfoodtox/editorial_content.py``'s ``EDITORIAL_CONTENT``
dataclasses DIRECTLY -- never the rendered Markdown draft bodies -- so the
runtime import path never parses Markdown and never needs the bulk
OpenFoodTox VM dataset or a pilot output directory present. Every
``EditorialEntry.effects``/``population_exceptions``/``identity``/
``purpose`` field used here is already the consumer-approved subset (the
same dataclasses ``evidence_bundle.py`` renders into the consumer draft);
``operator_only_notes`` is never read by this script, by construction.

Deliberately does NOT touch ``acceptable_daily_intake``, ``efsa_status``,
``fda_status``, ``risk_level`` or any dietary/scoring flag -- this
artifact only carries the fields
``app/seed/load_openfoodtox_pilot_content.py`` is allowed to write (see
that module's own docstring for the full, authoritative field list and
the reasoning).

Usage::

    python -m scripts.openfoodtox.build_app_pilot_content [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openfoodtox.editorial_content import EDITORIAL_CONTENT, EDITORIAL_CONTENT_VERSION  # noqa: E402

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_PATH = _BACKEND_ROOT / "app" / "seed" / "openfoodtox_pilot_profiles.json"

# Content-artifact format version -- bumped whenever the SHAPE of a
# profile entry changes (new/removed field), independent of
# EDITORIAL_CONTENT_VERSION (which tracks the underlying claim content).
_ARTIFACT_FORMAT_VERSION = 1

# Display identity for the one profile this pilot provisions as a brand
# new ingredient (E150d does not exist in any existing seed file) --
# content, not logic, so it lives here rather than in the import script.
_NEW_INGREDIENT_DISPLAY = {
    "E150d": {
        "id": "e150d_sulphite_ammonia_caramel",
        "common_name": {"en": "Sulphite ammonia caramel", "bg": "Сулфитно-амонячен карамел"},
        "category": {"en": "Food colour (caramel, Class IV)", "bg": "Хранителен оцветител (карамел, клас IV)"},
    }
}


def _join_effects(entry) -> tuple[str, str]:
    parts_en = [note.text.en for note in entry.effects]
    parts_bg = [note.text.bg for note in entry.effects]
    if entry.group_scope_note:
        parts_en.append(entry.group_scope_note.en)
        parts_bg.append(entry.group_scope_note.bg)
    return "\n\n".join(parts_en), "\n\n".join(parts_bg)


def _join_population_exceptions(entry) -> tuple[str, str]:
    parts_en = [exc.en for exc in entry.population_exceptions]
    parts_bg = [exc.bg for exc in entry.population_exceptions]
    return "\n\n".join(parts_en), "\n\n".join(parts_bg)


def _join_references(entry) -> str:
    lines = []
    for s in entry.external_sources:
        title = s.get("title", "")
        url = s.get("url", "")
        access_date = s.get("access_date", "")
        line = title
        if url:
            line += f" ({url})"
        if access_date:
            line += f"; accessed {access_date}"
        lines.append(line)
    return "\n".join(lines)


# E150d's group ADI is deliberately withheld from the consumer preview
# (docs/OPENFOODTOX_SOURCE_CLOSURE_TASK.md / pilot's own chemical-basis
# eligibility check -- see pilot/v8/adhoc_query_E150d_profile.json). This
# honestly explains the absence of a number instead of inferring one --
# never a substitute for a real, eligible numeric figure.
_DIETARY_GUIDANCE_OVERRIDES = {
    "E150d": {
        "en": (
            "No individual numeric intake limit is set for this specific colour class in this "
            "preview. EFSA's re-evaluation sets one limit shared across all four caramel colour "
            "classes together (see Health considerations) rather than a number for this class alone, "
            "so no single-substance figure is shown here."
        ),
        "bg": (
            "В този преглед не е посочена индивидуална числена граница на прием за този конкретен "
            "клас оцветител. Преоценката на ЕФСА определя обща граница, споделена от всичките четири "
            "класа карамелени оцветители заедно (вж. Здравни съображения), а не отделна стойност само "
            "за този клас, затова тук не е показана самостоятелна числена стойност."
        ),
    }
}


def build_artifact() -> dict:
    profiles = {}
    for e_number in ("E250", "E150d", "E330", "E951"):
        entry = EDITORIAL_CONTENT.get(e_number)
        if entry is None:
            raise RuntimeError(f"No EDITORIAL_CONTENT entry for required pilot identity {e_number!r}")

        health_en, health_bg = _join_effects(entry)
        cond_en, cond_bg = _join_population_exceptions(entry)
        guidance = _DIETARY_GUIDANCE_OVERRIDES.get(e_number, {"en": "", "bg": ""})

        profile = {
            "e_number": e_number,
            "description": {
                "en": entry.identity.en if entry.identity else "",
                "bg": entry.identity.bg if entry.identity else "",
            },
            "purpose_in_food": {
                "en": entry.purpose.en if entry.purpose else "",
                "bg": entry.purpose.bg if entry.purpose else "",
            },
            "health_concerns": {"en": health_en, "bg": health_bg},
            "effect_conditions": {"en": cond_en, "bg": cond_bg},
            "dietary_guidance": guidance,
            "references": _join_references(entry),
        }
        if e_number in _NEW_INGREDIENT_DISPLAY:
            profile["new_ingredient_display"] = _NEW_INGREDIENT_DISPLAY[e_number]
        profiles[e_number] = profile

    return {
        "artifact_format_version": _ARTIFACT_FORMAT_VERSION,
        "editorial_content_version": EDITORIAL_CONTENT_VERSION,
        "content_version": f"openfoodtox-app-pilot-v{EDITORIAL_CONTENT_VERSION}",
        "owner_publication_permission": True,
        "scientific_review_status": "not_reviewed",
        "translation_review_status": "not_reviewed",
        "allowlisted_e_numbers": ["E250", "E150d", "E330", "E951"],
        "profiles": profiles,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    content = json.dumps(build_artifact(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"

    if args.check:
        if not _OUTPUT_PATH.exists():
            print(f"CHECK FAILED: {_OUTPUT_PATH} does not exist.")
            return 1
        existing = _OUTPUT_PATH.read_text(encoding="utf-8")
        if existing == content:
            print(f"CHECK OK: {_OUTPUT_PATH} matches generated content exactly.")
            return 0
        print(f"CHECK FAILED: {_OUTPUT_PATH} does not match generated content.")
        return 1

    _OUTPUT_PATH.write_text(content, encoding="utf-8")
    print(f"EXPORTED: {_OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
