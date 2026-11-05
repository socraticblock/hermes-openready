"""Bounded subprocess execution.

One rule governs this module: **a call never raises and never hangs**. Every
failure mode a host can produce — binary absent, permission denied, timeout,
non-UTF-8 output, absurd output size — is returned as data, because the caller is
a guard that must fail closed rather than propagate an exception into the agent
loop.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAX_CAPTURE_CHARS = 200_000


@dataclass(frozen=True)
class RunResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    missing: bool = False
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.missing

    def json(self) -> Any:
        """Parse stdout as JSON. Returns ``None`` when it is not JSON."""
        text = self.stdout.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return None

    def summary(self, limit: int = 600) -> str:
        text = (self.stderr or self.stdout or "").strip()
        if len(text) > limit:
            text = text[:limit] + "…"
        return text


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    """Execute *argv* (never a shell string) with a hard timeout."""
    args = tuple(str(part) for part in argv)
    if not args:
        return RunResult(args, 127, "", "", missing=True, detail="empty argv")
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except FileNotFoundError:
        return RunResult(args, 127, "", "", missing=True,
                         detail=f"command not found: {args[0]}")
    except PermissionError:
        return RunResult(args, 126, "", "", missing=True,
                         detail=f"not executable: {args[0]}")
    except subprocess.TimeoutExpired:
        return RunResult(args, 124, "", "", timed_out=True,
                         detail=f"timed out after {timeout:g}s")
    except OSError as exc:
        return RunResult(args, 126, "", "", missing=True,
                         detail=f"{type(exc).__name__}: {exc}")

    stdout = proc.stdout.decode("utf-8", "replace")[:MAX_CAPTURE_CHARS]
    stderr = proc.stderr.decode("utf-8", "replace")[:MAX_CAPTURE_CHARS]
    return RunResult(args, proc.returncode, stdout, stderr)
