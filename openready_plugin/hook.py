"""The publish guard: a ``pre_tool_call`` hook.

It inspects exactly one tool — ``terminal`` — because that is the only surface
through which an agent can change a repository's public state. The handler is a
thin adapter: classify, gather evidence, apply the decision table, return a
directive. No writes, no state, no caching.

Failure policy: a guard that errors must not silently pass a publish command.
The handler therefore catches everything; if the command looked like a publish
attempt, the failure becomes a block, otherwise it becomes a pass-through.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import evidence, publish, settings

GUARDED_TOOL = "terminal"


def _repo_path(args: dict) -> Path:
    raw = args.get("workdir") or args.get("cwd") or os.getcwd()
    try:
        return Path(str(raw)).expanduser().resolve()
    except (OSError, ValueError):
        return Path(os.getcwd())


def pre_tool_call(**kwargs):
    """Return a block/approve directive for publish-shaped commands, else ``None``."""
    tool_name = kwargs.get("tool_name")
    if tool_name != GUARDED_TOOL:
        return None

    args = kwargs.get("args") or {}
    command = args.get("command") if isinstance(args, dict) else None
    if not isinstance(command, str) or not command.strip():
        return None

    try:
        intent = publish.classify(command)
    except Exception:
        # The classifier is pure and should never raise; if it does, the guard has
        # no idea what the command is, so every non-empty command fails closed.
        return {"action": "block", "message": _fail_closed(command, "classifier error")}

    if intent is None:
        return None

    try:
        conf = settings.load()
        repo = _repo_path(args)
        destination = intent.destination() or conf.destination
        found = evidence.lookup(repo, destination, conf)
        directive = publish.decide(intent, evidence=found)
        result = directive.as_hook_result()
        if result and conf.problems:
            result["message"] += "\nGuard configuration problems: " + "; ".join(conf.problems)
        return result
    except Exception as exc:
        return {"action": "block", "message": _fail_closed(command, f"{type(exc).__name__}: {exc}")}


def _fail_closed(command: str, detail: str) -> str:
    return (
        f"hermes-openready blocked `{command}`: the publish guard could not complete its "
        f"approval check ({detail}). It fails closed. Fix the guard configuration "
        f"(openready_bin / openready_python) or have the owner perform the change manually."
    )
