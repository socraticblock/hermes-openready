"""Bounded subprocess execution.

One rule governs this module: **a call never raises and never hangs**. Every
failure mode a host can produce — binary absent, permission denied, timeout,
non-UTF-8 output, absurd output size, a grandchild holding the pipe open — is
returned as data, because the caller is a guard that must fail closed rather than
propagate an exception into the agent loop.

Two bounds are enforced while the child runs, not after it exits:

* output is read in chunks and only the first ``MAX_CAPTURE_BYTES`` are kept, so a
  noisy process cannot exhaust memory before the cap applies;
* a timeout kills the whole process group, because a descendant that inherited
  stdout would otherwise keep the pipe open past the deadline.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

MAX_CAPTURE_BYTES = 200_000
_CHUNK = 65_536
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
    truncated: bool = False
    stdout_bytes: int = 0

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


def _drain(stream, limit: int) -> tuple[bytes, int]:
    """Read *stream* to EOF, keeping at most *limit* bytes. Returns (kept, total)."""
    kept: list[bytes] = []
    kept_len = 0
    total = 0
    try:
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if kept_len < limit:
                take = chunk[: limit - kept_len]
                kept.append(take)
                kept_len += len(take)
    except (ValueError, OSError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass
    return b"".join(kept), total


def run(
    argv: Sequence[str],
    *,
    timeout: float,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
) -> RunResult:
    """Execute *argv* (never a shell string) with a hard timeout and bounded capture."""
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

    captured: dict[str, tuple[bytes, int]] = {}

    def drain(name: str, stream) -> None:
        captured[name] = _drain(stream, MAX_CAPTURE_BYTES)

    readers = [
        threading.Thread(target=drain, args=("out", proc.stdout), daemon=True),
        threading.Thread(target=drain, args=("err", proc.stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_group(proc)
        try:
            proc.wait(timeout=_KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass

    for reader in readers:
        reader.join(timeout=_KILL_GRACE_SECONDS)

    out_bytes, out_total = captured.get("out", (b"", 0))
    err_bytes, err_total = captured.get("err", (b"", 0))
    if timed_out:
        return RunResult(args, 124, "", "", timed_out=True,
                         detail=f"timed out after {timeout:g}s",
                         stdout_bytes=out_total)

    return RunResult(
        args,
        proc.returncode,
        out_bytes.decode("utf-8", "replace"),
        err_bytes.decode("utf-8", "replace"),
        truncated=out_total > MAX_CAPTURE_BYTES or err_total > MAX_CAPTURE_BYTES,
        stdout_bytes=out_total,
    )
