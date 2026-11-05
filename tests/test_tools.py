"""Tool handler tests: envelopes, exit-code mapping, and the honesty note."""

from __future__ import annotations

import json

from openready_plugin import tools
from openready_plugin.evidence import Evidence
from openready_plugin.runner import RunResult
from openready_plugin.settings import Settings


def _settings(**overrides) -> Settings:
    base = dict(guard_enabled=True, destination="https://github.com/a/b",
                candidate_dir=".openready-candidate", licence="MIT",
                openready_bin="openready", openready_python="python3",
                timeout_seconds=120, approval_timeout_seconds=20)
    base.update(overrides)
    return Settings(**base)


def _result(rc: int, stdout: str = "", stderr: str = "", **kw) -> RunResult:
    return RunResult(("openready",), rc, stdout, stderr, **kw)


def test_doctor_maps_exit_codes(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run", lambda *a, **k: _result(2, '{"publication_status": "NOT_APPROVED"}'))
    payload = json.loads(tools.handle_doctor({"path": "/tmp"}))
    assert payload["exit_code"] == 2
    assert payload["status"] == "NEEDS_HUMAN"
    assert payload["ok"] is False
    assert payload["data"]["publication_status"] == "NOT_APPROVED"


def test_exit_zero_is_never_reported_as_safe_to_publish(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run", lambda *a, **k: _result(0, '{"ok": true}'))
    payload = json.loads(tools.handle_doctor({"path": "/tmp"}))
    assert payload["ok"] is True
    assert payload["publication"] == "NOT_APPROVED"
    assert "never means safe to publish" in payload["note"]


def test_missing_binary_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run",
                        lambda *a, **k: _result(127, missing=True, detail="command not found: openready"))
    payload = json.loads(tools.handle_status({"path": "/tmp"}))
    assert payload["ok"] is False
    assert "command not found" in payload["error"]


def test_gate_reports_approval(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.evidence, "lookup",
                        lambda repo, dest, conf: Evidence("approved", "found",
                                                          destination=dest, digest="sha256:" + "c" * 64))
    payload = json.loads(tools.handle_gate({"path": "/tmp"}))
    assert payload["would"] == "approve"
    assert payload["digest"].startswith("sha256:")


def test_gate_blocks_without_approval(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.evidence, "lookup",
                        lambda repo, dest, conf: Evidence("missing", "no approval", destination=dest))
    payload = json.loads(tools.handle_gate({"path": "/tmp"}))
    assert payload["would"] == "block"
    assert payload["ok"] is False


def test_gate_never_raises(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())

    def explode(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools.evidence, "lookup", explode)
    payload = json.loads(tools.handle_gate({"path": "/tmp"}))
    assert payload["status"] == "error"
    assert "boom" in payload["error"]


def test_availability_check(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings(openready_bin="/nonexistent/bin"))
    assert tools.openready_available() is False
    monkeypatch.setattr(tools.settings, "load", lambda: _settings(openready_bin="/bin/sh"))
    assert tools.openready_available() is True


def test_tool_schemas_are_well_formed():
    names = [name for name, _schema, _handler in tools.TOOLS]
    assert names == ["openready_status", "openready_doctor", "openready_candidate",
                     "openready_verify", "openready_gate"]
    for name, schema, handler in tools.TOOLS:
        assert schema["name"] == name
        assert schema["parameters"]["type"] == "object"
        assert callable(handler)
