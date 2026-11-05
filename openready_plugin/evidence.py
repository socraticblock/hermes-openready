"""Read-only approval lookup.

Open-Ready already answers the only question the guard needs: *does an owner
approval cover this exact (candidate digest, destination, licence)?* Its
``publisher`` module re-derives the digest from the frozen candidate's own bytes
and ignores any approval asserted inside the candidate, so this adapter must not
re-implement that logic — it asks the tool, and treats every non-answer as a
refusal.

Exit codes are Open-Ready's closed vocabulary (``status.py``): 0 ok, 1 fail,
2 needs human, 3 not run, 64 usage. Only 0 is an approval.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import runner
from .settings import Settings

APPROVED = "approved"
MISSING = "missing"
ERROR = "error"


@dataclass(frozen=True)
class Evidence:
    status: str
    detail: str = ""
    destination: str = ""
    candidate: str = ""
    digest: str = ""

    @property
    def approved(self) -> bool:
        return self.status == APPROVED


def _publisher_argv(settings: Settings, candidate: Path, destination: str, licence: str) -> list[str]:
    return [
        settings.openready_python,
        "-m",
        "openready.publisher",
        "check",
        "--candidate",
        str(candidate),
        "--destination",
        destination,
        "--licence",
        licence,
        "--json",
    ]


def _digest_from(stdout: str) -> str:
    data = None
    try:
        data = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        return ""
    if isinstance(data, dict):
        for key in ("candidate_digest", "digest"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("sha256:"):
                return value
        results = data.get("results")
        if isinstance(results, list):
            for item in results:
                if isinstance(item, dict) and str(item.get("digest", "")).startswith("sha256:"):
                    return str(item["digest"])
    return ""


def _recorded_digest(candidate: Path) -> str:
    """Digest recorded in ``CANDIDATE.json``.

    Only reported when the publisher check passed: that check re-derives the
    digest from the frozen bytes and fails on a mismatch, so a passing result is
    what makes the recorded value trustworthy. On its own it is self-reported.
    """
    try:
        data = json.loads((candidate / "CANDIDATE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("candidate_digest") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.startswith("sha256:") else ""


def lookup(repo: Path, destination: str, settings: Settings) -> Evidence:
    """Ask Open-Ready whether an owner approval covers this artifact."""
    candidate = settings.candidate_path(repo)
    if not candidate.is_dir():
        return Evidence(
            MISSING,
            detail=(
                f"no candidate at {candidate}. Export one first "
                f"(`openready candidate --path {repo}`)."
            ),
            destination=destination,
            candidate=str(candidate),
        )

    if not destination:
        return Evidence(
            ERROR,
            detail="destination unknown: pass a target or configure `destination`",
            candidate=str(candidate),
        )

    if shutil.which(settings.openready_python) is None:
        return Evidence(
            ERROR,
            detail=f"interpreter not found: {settings.openready_python}",
            destination=destination,
            candidate=str(candidate),
        )

    result = runner.run(
        _publisher_argv(settings, candidate, destination, settings.licence),
        timeout=settings.approval_timeout_seconds,
        cwd=str(repo),
    )
    digest = _digest_from(result.stdout)

    if result.timed_out:
        return Evidence(ERROR, detail=result.detail, destination=destination,
                        candidate=str(candidate), digest=digest)
    if result.missing:
        return Evidence(
            ERROR,
            detail=f"{result.detail} — the openready package must be importable by "
                   f"{settings.openready_python}",
            destination=destination,
            candidate=str(candidate),
        )
    if result.returncode == 0:
        return Evidence(
            APPROVED,
            detail="owner approval found",
            destination=destination,
            candidate=str(candidate),
            digest=digest or _recorded_digest(candidate),
        )
    if result.returncode in (1, 2, 3):
        return Evidence(
            MISSING,
            detail=f"publisher check exit {result.returncode}: {result.summary()}",
            destination=destination,
            candidate=str(candidate),
            digest=digest,
        )
    return Evidence(
        ERROR,
        detail=f"publisher check exit {result.returncode}: {result.summary()}",
        destination=destination,
        candidate=str(candidate),
        digest=digest,
    )
