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
)


BLOCK_SHAPES = [
    ("gh repo edit --visibility public", VISIBILITY),
    ("gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("gh repo edit --visibility=public -R socraticblock/example", VISIBILITY),
    ("gh repo edit -R socraticblock/example --visibility public --accept-visibility-change-consequences", VISIBILITY),
    ("gh repo create example --public", CREATE_PUBLIC),
    ("gh repo create --public --source .", CREATE_PUBLIC),
    ("gh repo create example --visibility public", CREATE_PUBLIC),
    ('bash -c "gh repo edit socraticblock/example --visibility public"', VISIBILITY),
    ("/usr/bin/gh repo edit socraticblock/example --visibility public", VISIBILITY),
    ("gh api -X PATCH /repos/socraticblock/example -f visibility=public", API_VISIBILITY),
    ("gh api --method PATCH repos/socraticblock/example --field visibility=public", API_VISIBILITY),
    # Untokenisable but unambiguous: must fail closed rather than pass.
    ('gh repo edit socraticblock/example --visibility public "unclosed', VISIBILITY),
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
    "npm publish --dry-run",
    "openready doctor --path .",
    "gh api /repos/socraticblock/example",
    "gh api -X PATCH /repos/socraticblock/example -f description=hello",
    "gh api -X PATCH /repos/socraticblock/example -f visibility=private",
    "cat README.md | grep public",
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


def test_unknown_target_has_no_destination():
    intent = classify("gh repo edit --visibility public")
    assert intent.target == ""
    assert intent.destination() == ""


class _Evidence:
    def __init__(self, status, detail="", digest="", destination=""):
        self.status = status
        self.detail = detail
        self.digest = digest
        self.destination = destination


def test_pass_when_not_a_publish_command():
    directive = decide(None, guard_enabled=True, evidence=_Evidence("missing"))
    assert directive.action is None
    assert directive.as_hook_result() is None


def test_disabled_guard_passes():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        guard_enabled=False,
        evidence=_Evidence("missing"),
    )
    assert directive.action is None


def test_approved_escalates_never_auto_allows():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        guard_enabled=True,
        evidence=_Evidence("approved", digest="sha256:" + "a" * 64),
    )
    assert directive.action == "approve"
    assert "sha256:" in directive.message
    assert directive.rule_key


def test_missing_evidence_blocks_with_instructions():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        guard_enabled=True,
        evidence=_Evidence("missing", detail="publisher check exit 1: no approval"),
    )
    assert directive.action == "block"
    assert "No owner approval" in directive.message
    assert "openready_candidate" in directive.message


def test_error_blocks_and_says_fail_closed():
    directive = decide(
        Intent(VISIBILITY, "a/b", "gh repo edit a/b --visibility public"),
        guard_enabled=True,
        evidence=_Evidence("error", detail="timed out after 20s"),
    )
    assert directive.action == "block"
    assert "fails closed" in directive.message
