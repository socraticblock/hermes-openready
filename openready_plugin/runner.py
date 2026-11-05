"""Bounded subprocess execution.

One rule governs this module: **a call never raises and never hangs**. Every
failure mode a host can produce — binary absent, permission denied, timeout,
non-UTF-8 output, absurd output size, a grandchild holding the pipe open — is
returned as data, because the caller is a guard that must fail closed rather than
propagate an exception into the agent loop.

Timeouts kill the whole process group, not just the direct child: a child that
spawns a descendant inheriting stdout would otherwise keep the pipe open and
``communicate()`` would wait past the deadline.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAX_CAPTURE_CHARS = 200_000
_KILL_GRACE_SECONDS = 5.0


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


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child and anything it started, without raising."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


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
        proc = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            start_new_session=True,
        )
    except FileNotFoundError:
        return RunResult(args, 127, "", "", missing=True,
                         detail=f"command not found: {args[0]}")
    except PermissionError:
        return RunResult(args, 126, "", "", missing=True,
                         detail=f"not executable: {args[0]}")
    except OSError as exc:
        return RunResult(args, 126, "", "", missing=True,
                         detail=f"{type(exc).__name__}: {exc}")

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            stdout_bytes, stderr_bytes = b"", b""
        return RunResult(args, 124, "", "", timed_out=True,
                         detail=f"timed out after {timeout:g}s")

    stdout = stdout_bytes.decode("utf-8", "replace")[:MAX_CAPTURE_CHARS]
    stderr = stderr_bytes.decode("utf-8", "replace")[:MAX_CAPTURE_CHARS]
    return RunResult(args, proc.returncode, stdout, stderr)
