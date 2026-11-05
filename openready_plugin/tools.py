"""Tool definitions and handlers.

Every handler returns a JSON string and never raises: a tool that throws turns a
recoverable condition into an agent-loop error. Handlers also never report "safe
to publish" — Open-Ready's own contract says exit 0 does not mean that, and the
adapter must not launder the distinction.
"""

from __future__ import annotations

import json
import os
import shutil
from functools import wraps
from pathlib import Path
from typing import Any, Callable

from . import evidence, publish, runner, settings

MAX_DATA_CHARS = 12_000

EMOJI = {
    "openready_status": "📦",
    "openready_doctor": "🩺",
    "openready_candidate": "📤",
    "openready_verify": "🔎",
    "openready_gate": "🚧",
}

_STATUS = {
    0: "OK",
    1: "FAIL",
    2: "NEEDS_HUMAN",
    3: "NOT_RUN",
    64: "USAGE",
}

_PUBLICATION_NOTE = (
    "exit 0 never means safe to publish: publication requires an owner approval "
    "bound to this exact candidate digest."
)


def _schema(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


_PATH_PROP = {
    "type": "string",
    "description": "Repository path (default: the current working directory).",
}


TOOLS: tuple[tuple[str, dict, Callable[[dict, Any], str]], ...] = ()


def _config() -> settings.Settings:
    return settings.load()


def _binary(settings_value: settings.Settings) -> str:
    return settings_value.openready_bin


def openready_available() -> bool:
    """check_fn: hide the tools when Open-Ready is not installed on this host."""
    try:
        binary = settings.load().openready_bin
    except Exception:
        return False
    if shutil.which(binary):
        return True
    try:
        return Path(binary).expanduser().is_file()
    except (RuntimeError, OSError, ValueError):
        # `~no-such-user/openready` makes expanduser raise; that is "not installed".
        return False


def _resolve(path_value: str | None) -> Path:
    raw = path_value or os.getcwd()
    return Path(raw).expanduser().resolve()


def _bounded(data: Any) -> dict[str, Any]:
    """Keep structured data when it fits; otherwise return a bounded, honest summary.

    Truncating JSON into a string silently destroys the contract a caller parses,
    so an oversized payload becomes a shape summary plus a capped text excerpt
    that says it was truncated.
    """
    if data is None:
        return {}
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) <= MAX_DATA_CHARS:
        return {"data": data}
    if isinstance(data, dict):
        summary = {
            key: {
                "type": type(value).__name__,
                "size": len(value) if isinstance(value, (list, dict, str)) else None,
            }
            for key, value in data.items()
        }
    else:
        summary = {"type": type(data).__name__, "size": len(data) if hasattr(data, "__len__") else None}
    return {
        "data_summary": summary,
        "data_text": text[:4000],
        "truncated": True,
        "full_chars": len(text),
    }


def _envelope(
    tool: str,
    result: runner.RunResult,
    *,
    data: Any = None,
    problems: tuple[str, ...] = (),
) -> str:
    payload = {
        "tool": tool,
        "ok": result.ok,
        "exit_code": result.returncode,
        "status": _STATUS.get(result.returncode, "UNKNOWN"),
        "publication": "NOT_APPROVED",
        "note": _PUBLICATION_NOTE,
    }
    payload.update(_bounded(data))
    if result.truncated:
        # The child wrote more than the capture cap; the caller must know the
        # payload is incomplete rather than assume an empty result.
        payload["truncated"] = True
        payload["captured_bytes"] = result.stdout_bytes
    if data is None and result.stdout.strip():
        # Exit 0 with output the caller cannot parse is not a usable result:
        # say so, and hand over a bounded excerpt instead of silence.
        payload["parsed"] = False
        payload["data_text"] = result.stdout[:4000]
    if problems:
        payload["settings_problems"] = list(problems)
    if not result.ok:
        payload["error"] = result.detail or result.summary()
    return json.dumps(payload, ensure_ascii=False)


def _error_envelope(tool: str, detail: str) -> str:
    return json.dumps(
        {
            "tool": tool,
            "ok": False,
            "status": "error",
            "publication": "NOT_APPROVED",
            "error": detail,
            "note": _PUBLICATION_NOTE,
        },
        ensure_ascii=False,
    )


def _guard(tool: str) -> Callable:
    """Make a handler's contract true: it returns JSON, it never raises.

    Argument validation upstream is not a guarantee — a direct call, a hook, or a
    schema change can hand a handler a path that makes ``Path.resolve`` throw.
    """

    def decorate(fn: Callable[[dict, Any], str]) -> Callable[[dict, Any], str]:
        @wraps(fn)
        def wrapper(args: dict | None, **kwargs: Any) -> str:
            try:
                return fn(args or {}, **kwargs)
            except Exception as exc:  # noqa: BLE001 - the contract is "never raise"
                return _error_envelope(tool, f"{type(exc).__name__}: {exc}")

        return wrapper

    return decorate


def _run_tool(
    tool: str,
    argv: list[str],
    *,
    cwd: Path,
    conf: settings.Settings,
) -> str:
    result = runner.run(argv, timeout=conf.timeout_seconds, cwd=str(cwd))
    return _envelope(tool, result, data=result.json(), problems=conf.problems)


@_guard("openready_status")
def handle_status(args: dict, **_kw) -> str:
    conf = _config()
    repo = _resolve(args.get("path"))
    argv = [_binary(conf), "status", "--json"]
    if args.get("path"):
        argv += ["--path", str(repo)]
    return _run_tool("openready_status", argv, cwd=repo, conf=conf)


@_guard("openready_doctor")
def handle_doctor(args: dict, **_kw) -> str:
    conf = _config()
    repo = _resolve(args.get("path"))
    argv = [_binary(conf), "doctor", "--path", str(repo), "--json"]
    return _run_tool("openready_doctor", argv, cwd=repo, conf=conf)


@_guard("openready_candidate")
def handle_candidate(args: dict, **_kw) -> str:
    conf = _config()
    repo = _resolve(args.get("path"))
    argv = [_binary(conf), "candidate", "--path", str(repo), "--json"]
    if args.get("out"):
        argv += ["--out", str(Path(args["out"]).expanduser().resolve())]
    return _run_tool("openready_candidate", argv, cwd=repo, conf=conf)


@_guard("openready_verify")
def handle_verify(args: dict, **_kw) -> str:
    conf = _config()
    raw = str(args.get("candidate") or "").strip()
    if not raw:
        return _error_envelope("openready_verify", "candidate is required (path to a candidate directory)")
    candidate = Path(raw).expanduser().resolve()
    argv = [_binary(conf), "verify", "--candidate", str(candidate), "--json"]
    if args.get("packet"):
        argv += ["--packet", str(Path(args["packet"]).expanduser().resolve())]
    if args.get("expected_digest"):
        argv += ["--expected-digest", str(args["expected_digest"])]
    return _run_tool("openready_verify", argv, cwd=candidate.parent, conf=conf)


@_guard("openready_gate")
def handle_gate(args: dict, **_kw) -> str:
    """Read-only: what would the guard decide for this destination right now?"""
    try:
        conf = _config()
        repo = _resolve(args.get("path"))
        destination = str(args.get("destination") or conf.destination or "")
        found = evidence.lookup(repo, destination, conf)
        directive = publish.decide(
            publish.Intent("visibility", "", "openready_gate"),
            evidence=found,
        )
        return json.dumps(
            {
                "tool": "openready_gate",
                "ok": found.approved,
                "status": found.status,
                "destination": found.destination,
                "candidate": found.candidate,
                "digest": found.digest,
                "detail": found.detail,
                "would": directive.action or "pass",
                "settings_problems": list(conf.problems),
                "note": _PUBLICATION_NOTE,
            },
            ensure_ascii=False,
        )
    except Exception as exc:  # a read-only tool still must not raise
        return _error_envelope("openready_gate", f"{type(exc).__name__}: {exc}")


TOOLS = (
    (
        "openready_status",
        _schema(
            "openready_status",
            "Report Open-Ready project metadata and the private registry. Read-only.",
            {"path": _PATH_PROP},
            [],
        ),
        handle_status,
    ),
    (
        "openready_doctor",
        _schema(
            "openready_doctor",
            "Run Open-Ready's findings report for a repository: what is wrong, what was not "
            "checked, and the current publication status. Read-only.",
            {"path": _PATH_PROP},
            [],
        ),
        handle_doctor,
    ),
    (
        "openready_candidate",
        _schema(
            "openready_candidate",
            "Export an UNAPPROVED release candidate for a private repository. Writes only the "
            "candidate directory; it never publishes anything.",
            {"path": _PATH_PROP,
             "out": {"type": "string", "description": "Candidate directory (default: <repo>/.openready-candidate)."}},
            [],
        ),
        handle_candidate,
    ),
    (
        "openready_verify",
        _schema(
            "openready_verify",
            "Verify a frozen candidate and emit its unapproved release packet. Read-only with "
            "respect to the source repository.",
            {
                "candidate": {"type": "string", "description": "Path to the candidate directory."},
                "packet": {"type": "string", "description": "Optional packet output path."},
                "expected_digest": {"type": "string",
                                    "description": "Digest recorded when the artifact was reviewed."},
            },
            ["candidate"],
        ),
        handle_verify,
    ),
    (
        "openready_gate",
        _schema(
            "openready_gate",
            "Read-only check of whether an owner approval covers publication of this repository's "
            "candidate to a destination. Changes nothing.",
            {
                "path": _PATH_PROP,
                "destination": {"type": "string",
                                "description": "Destination URL (default: configured or derived)."},
            },
            [],
        ),
        handle_gate,
    ),
)
