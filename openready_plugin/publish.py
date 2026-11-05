"""Publish-command grammar and the guard's decision table.

Both are pure functions. No I/O, no environment, no clock — which is what makes
the guard falsifiable: every case below is a value, so a test can enumerate the
whole table instead of hoping a scenario reproduces.

Grammar scope is deliberately narrow. It recognises the commands that change a
repository's *public surface*: `gh` visibility flips and public creation, the
equivalent REST call through `gh api` or `curl`, and those same commands behind
the wrappers an agent actually uses (`bash -lc`, `sudo`, `env -S`, `timeout`,
and shell command lists). It does not try to recognise every way an agent could
publish: a grammar that guesses produces false blocks, and a guard that blocks
honest work gets switched off. Substitutions (`$(echo gh) …`) and a script the
agent writes and then runs stay out of scope, and the README says so.
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

#: Fail-closed fallback when a segment cannot be tokenised. Deliberately blunt,
#: but it still has to name *public*: an unparseable private edit is not a publish.
_UNPARSEABLE = re.compile(
    r"\bgh\b.*\brepo\b.*\b(?:edit|create)\b.*\bpublic\b", re.IGNORECASE | re.DOTALL
)

_OPERATORS = ("&&", "||", ";;", ";", "|", "&", "\n")
_URL_REPOS = re.compile(
    r"(?:api\.github\.com|github\.com)/repos/([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)"
)
_URL_REPO_CREATE = re.compile(
    r"(?:api\.github\.com|github\.com)/(?:user|orgs/[A-Za-z0-9_.\-]+)/repos\b"
)


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
        # `rule_key` scopes an "always allow this" approval, so it is meaningful —
        # and permitted by the hook contract — only on an approve directive.
        if self.action == "approve" and self.rule_key:
            payload["rule_key"] = self.rule_key
        return payload


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def _flag_value(tokens: list[str], *names: str) -> str:
    """Value of ``--flag value``, ``--flag=value``, or an attached short flag (``-XPOST``)."""
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                return tokens[index + 1]
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
            if len(name) == 2 and name.startswith("-") and not name.startswith("--"):
                if token.startswith(name) and token != name and not token.startswith("--"):
                    return token[len(name):]
    return ""


def _flag_values(tokens: list[str], *names: str) -> list[str]:
    """Every value of every occurrence of the named flags."""
    values: list[str] = []
    for index, token in enumerate(tokens):
        for name in names:
            if token == name and index + 1 < len(tokens):
                values.append(tokens[index + 1])
            elif token.startswith(name + "="):
                values.append(token.split("=", 1)[1])
    return values


def _visibility(tokens: list[str]) -> str:
    """Effective visibility: any ``public`` wins, otherwise the last value given.

    Last-wins matches how the CLI parses a repeated flag; "any public wins" is the
    conservative reading, and the difference only ever makes the guard stricter.
    """
    values = [value.strip().lower() for value in _flag_values(tokens, "--visibility")]
    if not values:
        return ""
    if PUBLIC in values:
        return PUBLIC
    return values[-1]


def _strip_assignments(tokens: list[str]) -> list[str]:
    """Drop leading shell assignments: ``GH_TOKEN=x gh …`` is a gh command."""
    index = 0
    while index < len(tokens) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[index]):
        index += 1
    return tokens[index:]


def _has_flag(tokens: list[str], *names: str) -> bool:
    for token in tokens:
        if token in names:
            return True
        for name in names:
            if name.startswith("--") and token.startswith(name + "="):
                value = token.split("=", 1)[1].strip().lower()
                if value not in {"false", "0", "no"}:
                    return True
    return False


def _positionals(tokens: list[str]) -> list[str]:
    """Arguments that are neither flags nor the value of a value-taking flag."""
    value_flags = {
        "-R", "--repo", "--visibility", "--method", "-X", "-f", "-F", "--field",
        "--raw-field", "--input", "--source", "--remote", "--description",
        "--homepage", "--team", "--template", "--license", "--gitignore",
        "-H", "--header", "-d", "--data", "--data-raw", "--data-binary",
        "--request", "-u", "--user", "-o", "--output", "--json",
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
        elif len(token) > 2 and token[:2] in {"-f", "-F"} and not token.startswith("--"):
            fields.append(token[2:])
    return fields


def _data_payload(tokens: list[str]) -> str:
    chunks: list[str] = []
    for index, token in enumerate(tokens):
        if token in {"-d", "--data", "--data-raw", "--data-binary", "--json"} and index + 1 < len(tokens):
            chunks.append(tokens[index + 1])
        elif token.startswith(("--data=", "--data-raw=", "--data-binary=", "--json=")):
            chunks.append(token.split("=", 1)[1])
    return " ".join(chunks)


#: Wrappers that run another command, and how their own arguments are shaped.
_WRAPPERS = frozenset({"sudo", "doas", "command", "exec", "nohup", "nice", "timeout", "stdbuf", "env"})


def _strip_wrapper(name: str, tokens: list[str]) -> list[str] | None:
    """Return the tokens that follow a wrapper, or ``None`` when they cannot be parsed."""
    rest = tokens[1:]
    if name == "nohup":
        return rest
    if name == "env":
        index = 0
        while index < len(rest):
            token = rest[index]
            if token in {"-S", "--split-string"} and index + 1 < len(rest):
                inner = _split(rest[index + 1])
                if inner is None:
                    return None
                return inner + rest[index + 2:]
            if token.startswith("--split-string="):
                inner = _split(token.split("=", 1)[1])
                if inner is None:
                    return None
                return inner + rest[index + 1:]
            if token in {"-u", "--unset", "-C", "--chdir"}:
                index += 2
                continue
            if token.startswith("-") or "=" in token:
                index += 1
                continue
            return rest[index:]
        return []
    if name == "timeout":
        index = 0
        while index < len(rest) and rest[index].startswith("-"):
            index += 2 if rest[index] in {"-s", "--signal", "-k", "--kill-after"} else 1
        return rest[index + 1:] if index < len(rest) else []
    if name == "nice":
        index = 0
        while index < len(rest) and rest[index].startswith("-"):
            index += 2 if rest[index] in {"-n", "--adjustment"} else 1
        return rest[index:]
    # sudo / doas / command / exec / stdbuf: drop flags, plus the value of the
    # flags that take one.
    index = 0
    while index < len(rest) and rest[index].startswith("-"):
        index += 2 if rest[index] in {"-u", "--user", "-g", "--group", "-a", "--auth"} else 1
    return rest[index:]


def _split(command: str) -> list[str] | None:
    try:
        return shlex.split(command, posix=True)
    except ValueError:
        return None


def segments(command: str) -> list[str]:
    """Split a shell command line into the commands it would run, in order.

    A small state machine rather than ``shlex`` with ``punctuation_chars``: the
    latter also splits on ``:``, which mangles URLs. Quotes and backslash escapes
    are respected, so a publish command inside a string stays inside the string.
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            buf.append(char)
            if char == quote and command[index - 1] != "\\":
                quote = None
            index += 1
            continue
        if char in "\"'":
            quote = char
            buf.append(char)
            index += 1
            continue
        matched = next((op for op in _OPERATORS if command.startswith(op, index)), "")
        if matched:
            out.append("".join(buf))
            buf = []
            index += len(matched)
            continue
        buf.append(char)
        index += 1
    out.append("".join(buf))
    return [part.strip() for part in out if part.strip()]


def _skip_gh_globals(args: list[str]) -> list[str]:
    """Drop flags that `gh` accepts before the subcommand (``gh -R o/r repo edit``)."""
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in {"-R", "--repo", "--hostname"} else 1
    return args[index:]


def _repo_target(path: str) -> str:
    if "/repos/" in path:
        return path.split("/repos/", 1)[1].strip("/")
    if path.startswith("repos/"):
        return path[len("repos/"):].strip("/")
    return ""


def _classify_gh(args: list[str], global_repo: str, command: str) -> Intent | None:
    if len(args) >= 2 and args[0] == "repo" and args[1] in {"edit", "create"}:
        verb, rest = args[1], args[2:]
        target = global_repo or _target(rest)
        if verb == "edit" and _visibility(rest) == PUBLIC:
            return Intent(VISIBILITY, target, command)
        if verb == "create" and (_has_flag(rest, "--public") or _visibility(rest) == PUBLIC):
            return Intent(CREATE_PUBLIC, target, command)
        return None

    if not args or args[0] != "api":
        return None

    rest = args[1:]
    method = (_flag_value(rest, "--method", "-X") or "GET").upper()
    path = next(
        (token for token in _positionals(rest)
         if "/repos" in token or token.startswith("repos")),
        "",
    )
    if method not in {"PATCH", "POST"} or not path:
        return None

    public_field = any(
        field.lower().startswith("visibility=public") for field in _api_fields(rest)
    )
    is_update_path = "/repos/" in path or path.startswith("repos/")
    if public_field:
        if is_update_path:
            return Intent(API_VISIBILITY, _repo_target(path), command)
        return Intent(CREATE_PUBLIC, "", command)
    if _has_flag(rest, "--input") and is_update_path:
        # An opaque body could carry `visibility: public`; the guard cannot read
        # it, so the call is escalated instead of silently passed.
        return Intent(API_VISIBILITY, _repo_target(path), command)
    return None


def _classify_tokens(tokens: list[str], command: str, depth: int = 0) -> Intent | None:
    if not tokens or depth > 4:
        return None

    tokens = _strip_assignments(tokens)
    if not tokens:
        return None
    head = _basename(tokens[0])

    if head in SHELLS:
        # `bash -lc "…"`, `bash -ec "…"`, `sh -c "…"`: the command flag can be
        # stacked with other single-letter flags.
        for index, token in enumerate(tokens[1:], start=1):
            if token.startswith("-") and not token.startswith("--") and "c" in token[1:]:
                if index + 1 >= len(tokens):
                    return None
                inner = _split(tokens[index + 1])
                if inner is None:
                    return (
                        Intent(VISIBILITY, "", command)
                        if _UNPARSEABLE.search(tokens[index + 1])
                        else None
                    )
                return _classify_tokens(inner, command, depth + 1)
        return None

    if head in _WRAPPERS:
        rest = _strip_wrapper(head, tokens)
        if rest is None:
            return Intent(VISIBILITY, "", command) if _UNPARSEABLE.search(command) else None
        return _classify_tokens(rest, command, depth + 1) if rest else None

    if head == "curl":
        return _classify_curl(tokens[1:], command)

    if head != "gh":
        return None

    raw_args = tokens[1:]
    global_repo = _flag_value(raw_args, "-R", "--repo")
    args = _skip_gh_globals(raw_args)
    return _classify_gh(args, global_repo, command)


def _classify_curl(args: list[str], command: str) -> Intent | None:
    """A direct REST visibility change is a publish attempt whether or not `gh` runs it."""
    method = (_flag_value(args, "--request", "-X") or "GET").upper()
    if method not in {"PATCH", "POST"}:
        return None
    payload = _data_payload(args).lower()
    if "visibility" not in payload or PUBLIC not in payload:
        return None
    if method == "POST":
        # `POST /user/repos` or `POST /orgs/<org>/repos` creates a public repo.
        if any(_URL_REPO_CREATE.search(token) for token in args):
            return Intent(CREATE_PUBLIC, "", command)
        return None
    urls = [token for token in args if _URL_REPOS.search(token)]
    if not urls:
        return None
    match = _URL_REPOS.search(urls[0])
    return Intent(API_VISIBILITY, match.group(1) if match else "", command)


def classify(command: str) -> Intent | None:
    """Return the first publish intent in *command*, or ``None`` when there is none."""
    if not isinstance(command, str) or not command.strip():
        return None
    for segment in segments(command):
        tokens = _split(segment)
        if tokens is None:
            if _UNPARSEABLE.search(segment):
                return Intent(VISIBILITY, "", command)
            continue
        intent = _classify_tokens(tokens, command)
        if intent is not None:
            return intent
    return None


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
        # Scope the "always allow" key to this artifact: a generic key would let one
        # approval authorise a different candidate or destination later.
        digest = str(getattr(evidence, "digest", "") or "")
        key = f"hermes-openready:publish:{intent.target or 'unknown'}"
        if digest.startswith("sha256:"):
            key += f":{digest[7:19]}"
        return Directive(
            "approve",
            message=(
                f"hermes-openready: an owner approval covers {destination} "
                f"({digest or 'digest recorded'}). "
                "Publication is still the owner's decision — confirm to allow this command."
            ),
            rule_key=key,
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
        )

    return Directive(
        "block",
        message=(
            f"hermes-openready blocked `{intent.command}`: the approval check could not be "
            f"performed ({getattr(evidence, 'detail', 'unknown error')}). "
            "The guard fails closed — fix the check, then retry."
        ),
    )
