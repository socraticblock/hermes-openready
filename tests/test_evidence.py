"""Evidence lookup tests: every non-answer must read as a refusal."""

from __future__ import annotations

import json

import pytest

from openready_plugin import evidence
from openready_plugin.runner import RunResult
from openready_plugin.settings import Settings

DIGEST = "sha256:" + "d" * 64
CHECK_OK = json.dumps(
    {"command": "check", "exit_code": 0, "approval_file": "/tmp/approvals.json"}
)


def _settings(**overrides) -> Settings:
    base = dict(destination="", candidate_dir=".openready-candidate",
                licence="MIT", openready_bin="openready", openready_python="python3",
                timeout_seconds=120, approval_timeout_seconds=20)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    (tmp_path / ".openready-candidate").mkdir()
    (tmp_path / ".openready-candidate" / "CANDIDATE.json").write_text(
        json.dumps({"candidate_digest": DIGEST}), encoding="utf-8"
    )
    monkeypatch.setattr(evidence.shutil, "which", lambda _name: "/usr/bin/python3")
    return tmp_path


def _result(rc: int, stdout: str = "", stderr: str = "", **kw) -> RunResult:
    return RunResult(("python3",), rc, stdout, stderr, **kw)


def test_missing_candidate_is_a_refusal(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence.shutil, "which", lambda _name: "/usr/bin/python3")
    found = evidence.lookup(tmp_path, "https://github.com/a/b", _settings())
    assert found.status == evidence.MISSING
    assert "no candidate" in found.detail


def test_unknown_destination_is_an_error(repo):
    found = evidence.lookup(repo, "", _settings())
    assert found.status == evidence.ERROR
    assert "destination unknown" in found.detail


def test_missing_interpreter_is_an_error(repo, monkeypatch):
    monkeypatch.setattr(evidence.shutil, "which", lambda _name: None)
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR
    assert "interpreter not found" in found.detail


def test_exit_zero_is_approval_with_verified_digest(repo, monkeypatch):
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(0, stdout=CHECK_OK))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.APPROVED
    assert found.digest == DIGEST


def test_exit_code_false_is_not_success(repo, monkeypatch):
    """JSON `false` must not pass the `== 0` test (Python: False == 0)."""
    payload = json.dumps({"command": "check", "exit_code": False, "approval_file": "x"})
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(0, stdout=payload))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR


def test_malformed_digest_is_not_approval(repo, monkeypatch):
    payload = json.dumps({"command": "check", "exit_code": 0, "approval_file": "x"})
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(0, stdout=payload))
    monkeypatch.setattr(evidence, "_recorded_digest", lambda _c: "sha256:nothex")
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR
    assert "sha256" in found.detail


def test_exit_zero_without_check_json_is_an_error(repo, monkeypatch):
    """`openready_python=/bin/true` exits 0 with no output; that is not an approval."""
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(0))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR
    assert "did not report a check result" in found.detail


def test_exit_zero_without_approval_file_is_an_error(repo, monkeypatch):
    payload = json.dumps({"command": "check", "exit_code": 0})
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(0, stdout=payload))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR
    assert "without naming an approval store" in found.detail


@pytest.mark.parametrize("rc", [1, 2, 3])
def test_non_zero_is_refusal_and_never_borrows_the_recorded_digest(repo, monkeypatch, rc):
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(rc, stderr="no approval"))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.MISSING
    assert found.digest == ""


def test_timeout_is_an_error(repo, monkeypatch):
    monkeypatch.setattr(evidence.runner, "run",
                        lambda *a, **k: _result(124, timed_out=True, detail="timed out after 20s"))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR
    assert "timed out" in found.detail


def test_unexpected_exit_is_an_error(repo, monkeypatch):
    monkeypatch.setattr(evidence.runner, "run", lambda *a, **k: _result(99, stderr="boom"))
    found = evidence.lookup(repo, "https://github.com/a/b", _settings())
    assert found.status == evidence.ERROR


def test_argv_shape_is_module_invocation_not_a_shell_string(repo, monkeypatch):
    captured = {}

    def capture(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        return _result(0)

    monkeypatch.setattr(evidence.runner, "run", capture)
    evidence.lookup(repo, "https://github.com/a/b", _settings())
    argv = captured["argv"]
    assert argv[1:3] == ["-m", "openready.publisher"]
    assert "--candidate" in argv and "--destination" in argv and "--licence" in argv
    assert captured["cwd"] == str(repo)
