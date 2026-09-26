"""
Issue #23 (stage 3): Bulgarian review pack and explicit approval for ingredient
summaries.

    python -m app.seed.summary_review export [--file PATH]
        Read-only. Prints, from the committed content file, a Markdown pack a
        Bulgarian-speaking reviewer can check side by side: the English
        sections (source of truth), the machine-translated DRAFT and the
        English content hash the approval must quote. No database access.

    python -m app.seed.summary_review approve --subject KEY --language bg \
            --content-hash HASH --reviewer LABEL
        A deliberate, human-run action. Marks ONE stored translation
        REVIEWED, only when its `source_content_hash` and the summary's
        current `content_hash` both equal the hash the reviewer read. It
        never changes the text, never touches `translation_source` (a machine
        translation that a person reviewed stays MACHINE_TRANSLATED: origin
        and review are separate facts) and never sets `human_reviewed` on the
        scientific content, which is a different sign-off.

The loader never approves anything. Nothing here runs on start-up.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.database.session import AsyncSessionLocal
from app.models.ingredient_summary import IngredientSummary, IngredientSummaryLocalization
from app.seed.load_summaries import (
    SUMMARIES_SEED_FILE,
    _canonical_citations,
    _canonical_sections,
    read_seed_file,
)
from app.services.ingredient_summaries import content_hash


def render_review_pack(path: Path | None = None) -> str:
    lines = [
        "# Ingredient summary review pack (Bulgarian)",
        "",
        "The English sections are the source of truth. The Bulgarian text is a",
        "machine-translated DRAFT and is NOT served until a reviewer approves it.",
        "Check identifiers, numbers, years, units and that no claim was added or",
        "dropped. Approval quotes the English content hash shown for each subject.",
        "",
    ]
    for subject in read_seed_file(path):
        sections = _canonical_sections(subject["sections"])
        digest = content_hash(sections, _canonical_citations(subject["citations"]))
        lines += [f"## {subject['subjectKey']} ({subject['scope']})", "", f"English content hash: `{digest}`", ""]
        bg = (subject.get("localizations") or {}).get("bg")
        if bg is None:
            lines += ["No Bulgarian draft in the content file.", ""]
            continue
        for en, loc in zip(sections, bg["sections"]):
            title = en["kind"] + (f" ({en['jurisdiction']})" if en.get("jurisdiction") else "")
            lines += [f"### {title}", "", "EN:", "", en["text"], "", "BG (draft):", "", loc["text"], ""]
        lines += [
            "Approve with:",
            "",
            f"    python -m app.seed.summary_review approve --subject '{subject['subjectKey']}' "
            f"--language bg --content-hash {digest} --reviewer '<name or role>'",
            "",
        ]
    return "\n".join(lines)


async def approve(subject_key: str, language: str, digest: str, reviewer: str) -> str:
    reviewer = reviewer.strip()
    if not reviewer:
        return "refused: a reviewer label is required"
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(IngredientSummary).where(IngredientSummary.subject_key == subject_key))
        ).scalar_one_or_none()
        if row is None:
            return f"refused: no stored summary for {subject_key!r}"
        loc = await session.get(IngredientSummaryLocalization, (row.id, language))
        if loc is None:
            return f"refused: no stored {language!r} translation for {subject_key!r}"
        if row.content_hash != digest or loc.source_content_hash != digest:
            return (
                "refused: the hash you reviewed does not match the stored English content "
                "or the translation's source (the text changed after your review pack was made)"
            )
        if loc.translation_status == "REVIEWED":
            return "unchanged: already REVIEWED"
        loc.translation_status = "REVIEWED"
        loc.reviewed_at = datetime.now(timezone.utc)
        loc.reviewed_by = reviewer[:64]
        loc.updated_at = loc.reviewed_at
        await session.commit()
    return f"approved: {subject_key} {language} REVIEWED by {reviewer[:64]!r}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="summary_review")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export")
    export.add_argument("--file", type=Path, default=SUMMARIES_SEED_FILE)
    approve_cmd = sub.add_parser("approve")
    approve_cmd.add_argument("--subject", required=True)
    approve_cmd.add_argument("--language", required=True, choices=["bg"])
    approve_cmd.add_argument("--content-hash", required=True)
    approve_cmd.add_argument("--reviewer", required=True)
    args = parser.parse_args(argv)
    if args.command == "export":
        print(render_review_pack(args.file))
        return 0
    message = asyncio.run(approve(args.subject, args.language, args.content_hash, args.reviewer))
    print(message)
    return 0 if message.startswith(("approved", "unchanged")) else 1


if __name__ == "__main__":
    sys.exit(main())
