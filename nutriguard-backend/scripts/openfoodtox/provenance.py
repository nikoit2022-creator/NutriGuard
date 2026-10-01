"""Shared git provenance fingerprinting for every OpenFoodTox offline
output (catalogue, identity-audit, pilot profiles).

Recording a clean git SHA next to an output that was actually produced
by a *dirty* working tree would misrepresent exactly what code produced
it -- silently understating the real reproducibility gap. This module
always checks and reports both: the SHA, whether the tree was dirty at
run time, and (only when dirty) a content fingerprint of the uncommitted
diff, so a reader can tell "clean, fully reproducible from this SHA
alone" from "dirty -- reproducible only with this exact diff too" at a
glance, never silently blurring the two.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

_REPO_DIR = Path(__file__).resolve().parents[1]


def _run(args: list[str]) -> str | None:
    try:
        out = subprocess.run(args, cwd=_REPO_DIR, capture_output=True, text=True, timeout=15)
        return out.stdout if out.returncode == 0 else None
    except Exception:
        return None


def git_fingerprint() -> dict[str, Any]:
    sha = _run(["git", "rev-parse", "HEAD"])
    sha = sha.strip() if sha else None

    status = _run(["git", "status", "--porcelain"])
    dirty = bool(status and status.strip())

    dirty_diff_sha256 = None
    dirty_files: list[str] = []
    if dirty:
        diff = _run(["git", "diff", "HEAD"])
        if diff:
            dirty_diff_sha256 = hashlib.sha256(diff.encode("utf-8")).hexdigest()
        dirty_files = sorted(
            line[3:].strip() for line in (status or "").splitlines() if line.strip()
        )

    return {
        "git_sha": sha,
        "git_tree_dirty": dirty,
        "dirty_diff_sha256": dirty_diff_sha256,
        "dirty_files": dirty_files,
        "note": (
            "git_sha alone fully reproduces this output when git_tree_dirty is false; "
            "when true, dirty_diff_sha256 and dirty_files must also be preserved to "
            "reproduce it exactly -- the SHA by itself is insufficient."
            if dirty
            else "git_sha alone fully reproduces this output -- working tree was clean."
        ),
    }
