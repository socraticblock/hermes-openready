"""Tool handler tests: envelopes, exit-code mapping, and the honesty note."""

from __future__ import annotations

import json

import pytest

from openready_plugin import tools
from openready_plugin.evidence import Evidence
from openready_plugin.runner import RunResult
from openready_plugin.settings import Settings


def _settings(**overrides) -> Settings:
    base = dict(destination="https://github.com/a/b",
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


def test_oversized_json_is_reported_as_truncated(monkeypatch):
    """A capped payload must say it is incomplete, not look like a full result."""
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run", lambda *a, **k: RunResult(
        ("openready", "doctor"), 0, '{"pad": "' + "x" * 300_000 + '"}', "",
        truncated=True, stdout_bytes=300_010,
    ))
    payload = json.loads(tools.handle_doctor({"path": "/tmp"}))
    assert payload["truncated"] is True
    assert payload["captured_bytes"] == 300_010
    assert "data" not in payload


def test_unparseable_output_is_flagged(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run", lambda *a, **k: RunResult(
        ("openready", "doctor"), 0, "not json at all", "",
    ))
    payload = json.loads(tools.handle_doctor({"path": "/tmp"}))
    assert payload["parsed"] is False
    assert payload["data_text"] == "not json at all"


def test_verify_requires_a_candidate():
    payload = json.loads(tools.handle_verify({}))
    assert payload["status"] == "error"
    assert "candidate is required" in payload["error"]


def test_handlers_survive_an_unresolvable_path(monkeypatch):
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    monkeypatch.setattr(tools.runner, "run", lambda *a, **k: pytest.fail("must not run"))
    payload = json.loads(tools.handle_status({"path": "\x00"}))
    assert payload["status"] == "error"
    assert "ValueError" in payload["error"]


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


# --- the caller never names a write destination ---

def _no_run(monkeypatch):
    """Fail loudly if the CLI is invoked: a refusal must happen before the child."""
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return _result(0, "{}")

    monkeypatch.setattr(tools.runner, "run", fake)
    return calls


def _schema_for(name):
    return next(schema for schema_name, schema, _ in tools.TOOLS if schema_name == name)


def _project(tmp_path, marker=True):
    """A directory that looks like an Open-Ready project root with a candidate in it."""
    repo = tmp_path / "repo"
    (repo / ".openready-candidate").mkdir(parents=True)
    if marker:
        (repo / "openready.project.json").write_text('{"schema_version": "1.0"}')
    return repo


def test_candidate_takes_no_output_path():
    props = _schema_for("openready_candidate")["parameters"]["properties"]
    assert "out" not in props
    assert set(props) == {"path"}


def test_verify_takes_no_caller_supplied_packet_path():
    props = _schema_for("openready_verify")["parameters"]["properties"]
    assert "packet" not in props
    assert props["write_packet"]["type"] == "boolean"
    assert set(props) == {"candidate", "write_packet", "expected_digest"}


def test_candidate_refuses_a_legacy_out_argument_without_running_the_cli(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_candidate({"path": str(repo), "out": str(tmp_path / "elsewhere")}))
    assert payload["ok"] is False
    assert "`out` is not accepted" in payload["error"]
    assert payload["publication"] == "NOT_APPROVED"
    assert calls == []


def test_candidate_uses_the_cli_default_when_no_out_is_given(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_candidate({"path": str(repo)}))
    assert payload["ok"] is True
    assert "--out" not in calls[0][0][0]


def test_verify_refuses_a_legacy_packet_argument_without_running_the_cli(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "packet": "packet.json"}))
    assert payload["ok"] is False
    assert "`packet` is not accepted" in payload["error"]
    assert calls == []


def test_verify_does_not_write_a_packet_unless_asked(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify({"candidate": str(repo / ".openready-candidate")}))
    assert payload["ok"] is True
    assert "--packet" not in calls[0][0][0]
    assert "packet_path" not in payload


def test_write_packet_goes_to_the_fixed_path_inside_a_real_project_root(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": True}))
    expected = repo / ".openready" / "packets" / "packet.json"
    argv = calls[0][0][0]
    assert argv[argv.index("--packet") + 1] == str(expected)
    assert payload["packet_path"] == str(expected)
    assert expected.parent.is_dir()


def test_write_packet_refuses_a_parent_that_is_only_named_like_a_repository(tmp_path, monkeypatch):
    """A matching candidate basename is not evidence: the CLI's project marker is."""
    repo = _project(tmp_path, marker=False)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": True}))
    assert payload["ok"] is False
    assert "not an Open-Ready project root" in payload["error"]
    assert calls == []
    assert not (repo / ".openready").exists(), "no directory may be created before validation"


def test_write_packet_refuses_a_missing_candidate(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate-missing"), "write_packet": True}))
    assert payload["ok"] is False
    assert calls == []


def test_write_packet_refuses_a_symlinked_destination_directory(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / ".openready").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": True}))
    assert payload["ok"] is False
    assert "symbolic link" in payload["error"]
    assert calls == []
    assert not (elsewhere / "packets").exists()


def test_write_packet_refuses_a_dangling_packet_symlink(tmp_path, monkeypatch):
    """`exists()` is False for a dangling symlink; the check must not rely on it."""
    repo = _project(tmp_path)
    outside = tmp_path / "outside" / "created-by-the-writer.txt"
    packets = repo / ".openready" / "packets"
    packets.mkdir(parents=True)
    (packets / "packet.json").symlink_to(outside)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": True}))
    assert payload["ok"] is False
    assert "symbolic link" in payload["error"]
    assert calls == []


def test_write_packet_refuses_to_overwrite_an_existing_packet(tmp_path, monkeypatch):
    repo = _project(tmp_path)
    existing = repo / ".openready" / "packets" / "packet.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}")
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": True}))
    assert payload["ok"] is False
    assert "already exists" in payload["error"]
    assert calls == []


def test_write_packet_rejects_a_truthy_string(tmp_path, monkeypatch):
    """`"false"` is a truthy string; only a real boolean is accepted."""
    repo = _project(tmp_path)
    monkeypatch.setattr(tools.settings, "load", lambda: _settings())
    calls = _no_run(monkeypatch)
    payload = json.loads(tools.handle_verify(
        {"candidate": str(repo / ".openready-candidate"), "write_packet": "false"}))
    assert payload["ok"] is False
    assert "must be a boolean" in payload["error"]
    assert calls == []
