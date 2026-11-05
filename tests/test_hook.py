"""Hook tests: the decision table as it is actually reached from dispatch."""

from __future__ import annotations

import pytest

from openready_plugin import hook
from openready_plugin.evidence import Evidence
from openready_plugin.settings import Settings


def _settings(**overrides) -> Settings:
    base = dict(guard_enabled=True, destination="", candidate_dir=".openready-candidate",
                licence="MIT", openready_bin="openready", openready_python="python3",
                timeout_seconds=120, approval_timeout_seconds=20)
    base.update(overrides)
    return Settings(**base)


def _call(command, tool="terminal", workdir=None):
    args = {"command": command}
    if workdir:
        args["workdir"] = workdir
    return hook.pre_tool_call(tool_name=tool, args=args, task_id="t", session_id="s")


def test_ignores_other_tools(monkeypatch):
    monkeypatch.setattr(hook.evidence, "lookup", lambda *a, **k: pytest.fail("must not run"))
    assert _call("gh repo edit a/b --visibility public", tool="write_file") is None


def test_ignores_benign_commands(monkeypatch):
    monkeypatch.setattr(hook.evidence, "lookup", lambda *a, **k: pytest.fail("must not run"))
    assert _call("git status") is None
    assert _call("gh repo view a/b") is None


def test_blocks_without_approval(monkeypatch):
    monkeypatch.setattr(hook.settings, "load", lambda: _settings())
    monkeypatch.setattr(hook.evidence, "lookup",
                        lambda repo, dest, conf: Evidence("missing", "no approval"))
    result = _call("gh repo edit a/b --visibility public")
    assert result["action"] == "block"
    assert "No owner approval" in result["message"]
    assert set(result) == {"action", "message"}


def test_escalates_with_approval(monkeypatch):
    monkeypatch.setattr(hook.settings, "load", lambda: _settings())
    monkeypatch.setattr(hook.evidence, "lookup",
                        lambda repo, dest, conf: Evidence("approved", digest="sha256:" + "b" * 64))
    result = _call("gh repo edit a/b --visibility public")
    assert result["action"] == "approve"
    assert result["rule_key"].startswith("hermes-openready:publish:a/b:")
    assert set(result) == {"action", "message", "rule_key"}


def test_guard_disabled_passes(monkeypatch):
    monkeypatch.setattr(hook.settings, "load", lambda: _settings(guard_enabled=False))
    monkeypatch.setattr(hook.evidence, "lookup", lambda *a, **k: pytest.fail("must not run"))
    assert _call("gh repo edit a/b --visibility public") is None


def test_lookup_failure_blocks_publish_command(monkeypatch):
    monkeypatch.setattr(hook.settings, "load", lambda: _settings())

    def explode(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(hook.evidence, "lookup", explode)
    result = _call("gh repo edit a/b --visibility public")
    assert result["action"] == "block"
    assert "fails closed" in result["message"]


def test_settings_failure_blocks_publish_command(monkeypatch):
    def explode():
        raise RuntimeError("bad config")

    monkeypatch.setattr(hook.settings, "load", explode)
    result = _call("gh repo create x --public")
    assert result["action"] == "block"


def test_missing_command_argument_passes():
    assert hook.pre_tool_call(tool_name="terminal", args={}) is None
    assert hook.pre_tool_call(tool_name="terminal", args={"command": ""}) is None


def test_classifier_failure_blocks_even_a_benign_command(monkeypatch):
    """If classification breaks, the guard has no idea what the command is."""
    def explode(_command):
        raise RuntimeError("classifier bug")

    monkeypatch.setattr(hook.publish, "classify", explode)
    result = _call("git status")
    assert result["action"] == "block"
    assert "fails closed" in result["message"]
