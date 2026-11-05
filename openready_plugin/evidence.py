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
import os
import re
import shutil
import tempfile
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


#: Credentials the publisher never needs. A guard that asks "is this approved?"
#: must not hand the checked repository's code a token it could publish with.
_CREDENTIAL_VARS = frozenset({
    "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN",
})


def _child_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in _CREDENTIAL_VARS}


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
                if not isinstance(item, dict):
                    continue
                for key in ("candidate_digest", "digest"):
                    value = item.get(key)
                    if isinstance(value, str) and value.startswith("sha256:"):
                        return value
    return ""


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

    # Run from a neutral directory: `python -m` puts the current directory first on
    # sys.path, so a repository containing `openready/publisher.py` could answer its
    # own approval check. The guard must not execute the code it is judging.
    scratch = tempfile.mkdtemp(prefix="openready-check-")
    try:
        result = runner.run(
            _publisher_argv(settings, candidate, destination, settings.licence),
            timeout=settings.approval_timeout_seconds,
            cwd=scratch,
            env=_child_env(),
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    digest = _digest_from(result.stdout)

    if result.timed_out:
        return Evidence(ERROR, detail=result.detail, destination=destination,
                        candidate=str(candidate), digest=digest)
    if result.missing:
        if not repo.is_dir():
            detail = f"repository path does not exist: {repo}"
        else:
            detail = (f"{result.detail} — the openready package must be importable by "
                      f"{settings.openready_python}")
        return Evidence(
            ERROR,
            detail=detail,
            destination=destination,
            candidate=str(candidate),
        )
    if result.returncode == 0:
        # Exit 0 alone is not an approval: a misconfigured interpreter (or any
        # executable that succeeds) would otherwise read as "owner approved".
        # The publisher's own JSON must say it ran `check` and name the store.
        # `exit_code` is checked as a strict int: Python's `False == 0`, so a
        # JSON `false` must not be read as success.
        data = result.json()
        code = data.get("exit_code") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or data.get("command") != "check"
            or not isinstance(code, int)
            or isinstance(code, bool)
            or code != 0
        ):
            return Evidence(
                ERROR,
                detail=(
                    "publisher check exited 0 but did not report a check result; "
                    f"is `{settings.openready_python}` really running openready?"
                ),
                destination=destination,
                candidate=str(candidate),
            )
        approval_file = data.get("approval_file")
        if not isinstance(approval_file, str) or not approval_file.strip():
            return Evidence(
                ERROR,
                detail="publisher check exited 0 without naming an approval store; refusing to read that as an approval",
                destination=destination,
                candidate=str(candidate),
            )
        # An approval is bound to an artifact. Without a digest the evidence cannot
        # be bound to anything, so it is not an approval — the module's own claim.
        if not digest:
            return Evidence(
                ERROR,
                detail=(
                    "publisher check exited 0 without reporting a candidate digest; "
                    "an approval that names no artifact cannot authorise one"
                ),
                destination=destination,
                candidate=str(candidate),
            )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return Evidence(
                ERROR,
                detail=f"publisher check reported a digest that is not sha256:<64 hex>: {digest[:40]!r}",
                destination=destination,
                candidate=str(candidate),
            )
        return Evidence(
            APPROVED,
            detail="owner approval found",
            destination=destination,
            candidate=str(candidate),
            digest=digest,
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
