"""Publish-command grammar and the guard's decision table.

Both are pure functions. No I/O, no environment, no clock — which is what makes
the guard falsifiable: every case below is a value, so a test can enumerate the
whole table instead of hoping a scenario reproduces.

Grammar scope is deliberately narrow. It recognises the commands that change a
repository's *public surface* (visibility, public creation, the equivalent API
call). It does not try to recognise every way an agent could publish, because a
grammar that guesses produces false blocks, and a guard that blocks honest work
gets switched off.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
PUBLIC = "public"

VISIBILITY = "visibility"
CREATE_PUBLIC = "create_public"
API_VISIBILITY = "api_visibility"

#: Fail-closed fallback when the command cannot be tokenised. Deliberately blunt:
#: a string that cannot be parsed but mentions both terms is treated as a publish
#: attempt, because the alternative is letting it through unexamined.
_UNPARSEABLE = re.compile(r"\bgh\b.*\brepo\b.*\b(?:edit|create)\b", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class Intent:
    """A command that would change a repository's public surface."""

    kind: str
    target: str
    command: str

    def destination(self) -> str:
        """GitHub URL for :attr:`target`, or ``""`` when the target is unknown."""
        if not self.target:
            return ""
        if "://" in self.target:
            return self.target.rstrip("/")
        return f"https://github.com/{self.target.strip('/')}"


@dataclass(frozen=True)
class Directive:
    """What the guard tells the dispatcher. ``action`` is ``None`` for pass-through."""

    action: str | None
    message: str = ""
    rule_key: str = ""

    def as_hook_result(self) -> dict | None:
        if self.action is None:
            return None
        payload = {"action": self.action, "message": self.message}
        if self.rule_key:
            payload["rule_key"] = self.rule_key
        return payload


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _flag_value(tokens: list[str], *names: str) -> str:
    """Value of the first ``--flag value`` or ``--flag=value`` in *tokens*."""
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return ""


def _has_flag(tokens: list[str], *names: str) -> bool:
    return any(token in names for token in tokens)


def _positionals(tokens: list[str]) -> list[str]:
    """Arguments that are neither flags nor the value of a value-taking flag."""
    value_flags = {
        "-R", "--repo", "--visibility", "--method", "-X", "-f", "-F", "--field",
        "--raw-field", "--input", "--source", "--remote", "--description",
        "--homepage", "--team", "--template", "--license", "--gitignore",
    }
    out: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in value_flags:
            skip = True
            continue
        if token.startswith("-"):
            continue
        out.append(token)
    return out


def _target(tokens: list[str]) -> str:
    explicit = _flag_value(tokens, "-R", "--repo")
    if explicit:
        return explicit
    for candidate in _positionals(tokens):
        if re.fullmatch(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", candidate):
            return candidate
    return ""


def _api_fields(tokens: list[str]) -> list[str]:
    fields: list[str] = []
    for index, token in enumerate(tokens):
        if token in {"-f", "-F", "--field", "--raw-field"} and index + 1 < len(tokens):
            fields.append(tokens[index + 1])
        elif token.startswith(("--field=", "--raw-field=")):
            fields.append(token.split("=", 1)[1])
    return fields


def _classify_tokens(tokens: list[str], command: str, depth: int = 0) -> Intent | None:
    if not tokens or depth > 3:
        return None

    head = _basename(tokens[0])
    if head in SHELLS:
        for index, token in enumerate(tokens):
            if token == "-c" and index + 1 < len(tokens):
                inner = _split(tokens[index + 1])
                if inner is None:
                    return Intent(VISIBILITY, "", command) if _UNPARSEABLE.search(tokens[index + 1]) else None
                return _classify_tokens(inner, command, depth + 1)
        return None

    if head == "env":
        rest = [token for token in tokens[1:] if not token.startswith("-") and "=" not in token]
        return _classify_tokens(rest, command, depth + 1) if rest else None

    if head != "gh":
        return None

    args = tokens[1:]
    if len(args) >= 2 and args[0] == "repo" and args[1] == "edit":
        rest = args[2:]
        if _flag_value(rest, "--visibility").lower() == PUBLIC:
            return Intent(VISIBILITY, _target(rest), command)
        return None

    if len(args) >= 2 and args[0] == "repo" and args[1] == "create":
        rest = args[2:]
        if _has_flag(rest, "--public") or _flag_value(rest, "--visibility").lower() == PUBLIC:
            return Intent(CREATE_PUBLIC, _target(rest), command)
        return None

    if args and args[0] == "api":
        rest = args[1:]
        method = (_flag_value(rest, "--method", "-X") or "GET").upper()
        path = next(
            (token for token in _positionals(rest)
             if "/repos/" in token or token.startswith("repos/")),
            "",
        )
        if method in {"PATCH", "POST"} and path and any(
            field.lower().startswith("visibility=public") for field in _api_fields(rest)
        ):
            target = path.split("/repos/", 1)[1] if "/repos/" in path else path[len("repos/"):]
            return Intent(API_VISIBILITY, target.strip("/"), command)
        return None

    return None


def _split(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return None


def classify(command: str) -> Intent | None:
    """Return the publish intent of *command*, or ``None`` when it is not one."""
    if not isinstance(command, str) or not command.strip():
        return None
    tokens = _split(command)
    if tokens is None:
        return Intent(VISIBILITY, "", command) if _UNPARSEABLE.search(command) else None
    return _classify_tokens(tokens, command)


def decide(intent: Intent | None, *, guard_enabled: bool, evidence) -> Directive:
    """The whole decision table.

    ``evidence`` is any object with ``status`` (``approved`` / ``missing`` /
    ``error``), ``detail`` and ``digest`` attributes.
    """
    if intent is None:
        return Directive(None)
    if not guard_enabled:
        return Directive(
            None,
            message="hermes-openready guard is disabled in this profile (guard_enabled: false)",
        )

    destination = intent.destination() or getattr(evidence, "destination", "") or "the target repository"

    if getattr(evidence, "status", "error") == "approved":
        return Directive(
            "approve",
            message=(
                f"hermes-openready: an owner approval covers {destination} "
                f"({getattr(evidence, 'digest', '') or 'digest recorded'}). "
                "Publication is still the owner's decision — confirm to allow this command."
            ),
            rule_key="hermes-openready:publish",
        )

    if getattr(evidence, "status", "") == "missing":
        return Directive(
            "block",
            message=(
                f"hermes-openready blocked `{intent.command}`.\n"
                f"No owner approval covers {destination}.\n"
                f"{getattr(evidence, 'detail', '')}\n"
                "Next: openready_candidate → openready_verify, then the owner records the "
                "approval with `python -m openready.publisher record`."
            ),
            rule_key="hermes-openready:publish",
        )

    return Directive(
        "block",
        message=(
            f"hermes-openready blocked `{intent.command}`: the approval check could not be "
            f"performed ({getattr(evidence, 'detail', 'unknown error')}). "
            "The guard fails closed — fix the check, then retry."
        ),
        rule_key="hermes-openready:publish",
    )
