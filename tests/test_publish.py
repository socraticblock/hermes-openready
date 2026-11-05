"""Grammar and decision-table tests.

The grammar is a pure function, so the test enumerates the table rather than
simulating a shell. Both directions matter: a missed publish shape is a leak, and
a false positive teaches the owner to disable the guard.
"""

from __future__ import annotations

import pytest

from openready_plugin.publish import (
    API_VISIBILITY,
    CREATE_PUBLIC,
    VISIBILITY,
    Intent,
    classify,
    decide,
    segments,
)


BLOCK_SHAPES = [
    # direct gh
    ("gh repo edit --visibility public", VISIBILITY),
    ("gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("gh repo edit --visibility=public -R socraticblock/example", VISIBILITY),
    ("gh repo edit -R socraticblock/example --visibility public --accept-visibility-change-consequences", VISIBILITY),
    ("gh repo create example --public", CREATE_PUBLIC),
    ("gh repo create --public --source .", CREATE_PUBLIC),
    ("gh repo create example --visibility public", CREATE_PUBLIC),
    ("gh repo create example --public=true", CREATE_PUBLIC),
    # absolute paths and shells
    ("/usr/bin/gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ('bash -c "gh repo edit socraticblock/example --visibility public"', VISIBILITY),
    ('bash -lc "gh repo edit socraticblock/example --visibility public"', VISIBILITY),
    ('bash -ec "gh repo create example --public"', CREATE_PUBLIC),
    ('sh -c "gh repo create example --public"', CREATE_PUBLIC),
    ('env -S "gh repo edit socraticblock/example --visibility public"', VISIBILITY),
    # wrappers
    ("sudo gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("timeout 30 gh repo edit socraticblock/example --visibility public", VISIBILITY),
    # command lists
    ("true && gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("git status; gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("git fetch || gh repo create example --public", CREATE_PUBLIC),
    # API surface, through gh and through curl
    ("gh api -X PATCH /repos/socraticblock/example -f visibility=public", API_VISIBILITY),
    ("gh api --method PATCH repos/socraticblock/example --field visibility=public", API_VISIBILITY),
    ("gh api -XPOST /repos/socraticblock/example -f visibility=public", API_VISIBILITY),
    (
        'curl -X PATCH -H "Authorization: Bearer x" '
        "https://api.github.com/repos/socraticblock/example "
        "-d '{\"visibility\":\"public\"}'",
        API_VISIBILITY,
    ),
    # Untokenisable but unambiguous: must fail closed rather than pass.
    ('gh repo edit socraticblock/example --visibility public "unclosed', VISIBILITY),
    ('env -S "gh repo edit socraticblock/example --visibility public \'unclosed"', VISIBILITY),
    # shell assignment prefixes, repeated flags, global flags
    ("GH_TOKEN=x gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("gh repo edit socraticblock/example --visibility private --visibility public", VISIBILITY),
    ("gh -R socraticblock/example repo edit --visibility public", VISIBILITY),
    ("env -u FOO gh repo edit socraticblock/example --visibility public", VISIBILITY),
    # public creation and opaque bodies over the API
    ("gh api -X POST /user/repos -f name=example -f visibility=public", CREATE_PUBLIC),
    (
        'curl -X POST https://api.github.com/user/repos -d \'{"name":"x","visibility":"public"}\'',
        CREATE_PUBLIC,
    ),
    ("gh api -X PATCH /repos/socraticblock/example --input payload.json", API_VISIBILITY),
    ("gh api -X PATCH /repos/socraticblock/example -fvisibility=public", API_VISIBILITY),
    # wrapper chains and compound commands inside a shell
    ("sudo sudo sudo sudo sudo gh repo edit a/b --visibility public", VISIBILITY),
    ('bash -lc "true; gh repo edit a/b --visibility public"', VISIBILITY),
    ('sh -c "git status; gh repo create x --public"', CREATE_PUBLIC),
    # curl bodies: opaque file, urlencoded field, GraphQL mutation
    ("curl -X PATCH https://api.github.com/repos/a/b --data @payload.json", API_VISIBILITY),
    ("curl -X PATCH https://api.github.com/repos/a/b --data-urlencode visibility=public", API_VISIBILITY),
    (
        "gh api graphql -f query='mutation { updateRepository(input: {visibility: PUBLIC}) }'",
        API_VISIBILITY,
    ),
    # REST creation defaults to public: silence is a publish
    ("gh api /user/repos -f name=example", CREATE_PUBLIC),
    ("gh api -X POST /orgs/acme/repos -f name=example", CREATE_PUBLIC),
    (
        'curl -X POST https://api.github.com/user/repos -d \'{"name":"x"}\'',
        CREATE_PUBLIC,
    ),
    (
        'curl --json \'{"name":"x"}\' https://api.github.com/user/repos',
        CREATE_PUBLIC,
    ),
    # `private: false` is the other spelling of a visibility flip
    ("gh api -X PATCH /repos/a/b -f private=false", API_VISIBILITY),
    (
        'curl -X PATCH https://api.github.com/repos/a/b -d \'{"private":false}\'',
        API_VISIBILITY,
    ),
    # quoting the tokeniser cannot resolve is not evidence of "private"
    ("gh repo edit a/b --visibility $'public'", VISIBILITY),
    ("gh repo edit a/b --visibility=pub$'lic'", VISIBILITY),
    # shell syntax that runs a command without being the command
    ("time gh repo edit a/b --visibility public", VISIBILITY),
    ("! gh repo edit a/b --visibility public", VISIBILITY),
    ("{ gh repo edit a/b --visibility public; }", VISIBILITY),
    ("(gh repo edit a/b --visibility public)", VISIBILITY),
    ("setsid gh repo edit a/b --visibility public", VISIBILITY),
    ("script -qec 'gh repo edit a/b --visibility public' /dev/null", VISIBILITY),
    ("if true; then gh repo edit a/b --visibility public; fi", VISIBILITY),
    ("for i in 1; do gh repo create x --public; done", CREATE_PUBLIC),
    # curl bodies: attached short flags and file-backed field values
    (
        "curl -XPATCH https://api.github.com/repos/a/b -d'{\"visibility\":\"public\"}'",
        API_VISIBILITY,
    ),
    (
        "curl -XPATCH https://api.github.com/repos/a/b --data-urlencode visibility@payload.txt",
        API_VISIBILITY,
    ),
    ("curl -XPATCH https://api.github.com/repos/a/b -d@body.json", API_VISIBILITY),
    # GraphQL mutations whose body lives in a file
    ("gh api graphql --input mutation.json", API_VISIBILITY),
    ("gh api graphql -F query=@mutation.graphql", API_VISIBILITY),
    (
        "curl -X POST https://api.github.com/graphql --json @mutation.json",
        API_VISIBILITY,
    ),
    ("gh api -X PATCH /repos/a/b -F visibility=@file", API_VISIBILITY),
    # a wrapper chain past the budget is unclassifiable, so it is refused
    ("env " * 65 + "curl -X PATCH https://api.github.com/repos/a/b --data @payload.json",
     VISIBILITY),
]


PASS_SHAPES = [
    "gh repo view socraticblock/example",
    "gh repo edit --visibility private",
    "gh repo edit socraticblock/example --description hello",
    "gh repo create example --private",
    "gh pr create --title x --body y",
    "git push origin main",
    "git status --short",
    'echo "gh repo edit --visibility public"',
    'bash -lc "echo gh repo edit --visibility public"',
    "npm publish --dry-run",
    "openready doctor --path .",
    "gh api /repos/socraticblock/example",
    "gh api -X PATCH /repos/socraticblock/example -f description=hello",
    "gh api -X PATCH /repos/socraticblock/example -f visibility=private",
    "cat README.md | grep public",
    # wrappers and command lists that do not publish
    "sudo gh repo view socraticblock/example",
    "timeout 30 gh repo view socraticblock/example",
    "nice -n 5 git status",
    'env -S "git status"',
    "true && git status",
    "gh repo view a/b | grep public",
    # REST reads and non-visibility writes
    "curl https://api.github.com/repos/socraticblock/example",
    "curl -X PATCH https://api.github.com/repos/socraticblock/example -d '{\"description\":\"x\"}'",
    # unparseable but private: not a publish
    'gh repo edit a/b --visibility private "unclosed',
    # same wrappers/prefixes, but no publish verb
    "GH_TOKEN=x gh repo view a/b",
    "gh -R a/b repo view",
    "env -u FOO git status",
    # an explicitly private create is not a publish
    "gh api -X POST /user/repos -f name=example -f private=true",
    'curl -X POST https://api.github.com/user/repos -d \'{"name":"x","private":true}\'',
    "gh repo create example",
    # REST writes that do not touch visibility
    "gh api -X PATCH /repos/socraticblock/example -f description=x",
    "curl -XPATCH https://api.github.com/repos/a/b -d'{\"description\":\"x\"}'",
    # shell syntax in front of a harmless command
    "time gh repo view a/b",
    "(gh repo view a/b)",
    "",
]


@pytest.mark.parametrize("command,kind", BLOCK_SHAPES)
def test_publish_shapes_are_classified(command, kind):
    intent = classify(command)
    assert intent is not None, command
    assert intent.kind == kind
    assert intent.command == command


@pytest.mark.parametrize("command", PASS_SHAPES)
def test_non_publish_shapes_pass(command):
    assert classify(command) is None, command


def test_target_and_destination():
    intent = classify("gh repo edit socraticblock/example --visibility public")
    assert intent.target == "socraticblock/example"
    assert intent.destination() == "https://github.com/socraticblock/example"


def test_curl_target_is_parsed():
    intent = classify(
        "curl -X PATCH https://api.github.com/repos/socraticblock/example "
        "-d '{\"visibility\":\"public\"}'"
    )
    assert intent.target == "socraticblock/example"


def test_unknown_target_has_no_destination():
    intent = classify("gh repo edit --visibility public")
    assert intent.target == ""
    assert intent.destination() == ""


def test_segments_respect_quotes():
    assert segments('echo "a; b" && gh repo view x') == ['echo "a; b"', "gh repo view x"]
    assert segments("git status; gh repo view x") == ["git status", "gh repo view x"]


class _Evidence:
    def __init__(self, status, detail="", digest="", destination=""):
        self.status = status
        self.detail = detail
        self.digest = digest
        self.destination = destination


def test_pass_when_not_a_publish_command():
    directive = decide(None, evidence=_Evidence("missing"))
    assert directive.action is None
    assert directive.as_hook_result() is None


def test_disabled_guard_does_not_exist():
    """There is no config flag that turns the guard into a pass-through."""
    from openready_plugin import settings

    assert "guard_enabled" not in settings.DEFAULTS


def test_approved_escalates_never_auto_allows():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        evidence=_Evidence("approved", digest="sha256:" + "a" * 64),
    )
    assert directive.action == "approve"
    assert "sha256:" in directive.message
    # The always-allow key is scoped to this target and artifact, not generic.
    assert "a/b" in directive.rule_key
    assert "a" * 12 in directive.rule_key


def test_missing_evidence_blocks_with_instructions():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        evidence=_Evidence("missing", detail="publisher check exit 1: no approval"),
    )
    assert directive.action == "block"
    assert "No owner approval" in directive.message
    assert "openready_candidate" in directive.message


def test_error_blocks_and_says_fail_closed():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        evidence=_Evidence("error", detail="timed out after 20s"),
    )
    assert directive.action == "block"
    assert "fails closed" in directive.message


def test_block_results_carry_no_rule_key():
    """`rule_key` scopes an approval, so it does not belong on a block directive."""
    for status in ("missing", "error"):
        directive = decide(
            Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
                evidence=_Evidence(status, detail="x"),
        )
        assert set(directive.as_hook_result()) == {"action", "message"}
