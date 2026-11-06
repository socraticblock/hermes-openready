"""The child environment is an allowlist, and it is the default for every child."""

from __future__ import annotations

import json
import sys

from openready_plugin import childenv, runner


def test_allowlist_keeps_what_a_child_needs() -> None:
    seen = childenv.minimal(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/x",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LC_CTYPE": "C.UTF-8",
            "VIRTUAL_ENV": "/venv",
            "TMPDIR": "/tmp",
            "TMP": "/tmp",
            "TEMP": "/tmp",
            "OPENREADY_STORE": "/store",
        }
    )
    assert seen == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/x",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "LC_CTYPE": "C.UTF-8",
        "VIRTUAL_ENV": "/venv",
        "TMPDIR": "/tmp",
        "TMP": "/tmp",
        "TEMP": "/tmp",
        "OPENREADY_STORE": "/store",
    }


def test_credentials_are_withheld_by_default_not_by_name() -> None:
    """A secret nobody remembered to strip is still withheld: the rule is an allowlist."""
    seen = childenv.minimal(
        {
            "PATH": "/usr/bin",
            "ANTHROPIC_API_KEY": "sk-ant",
            "OPENAI_API_KEY": "sk",
            "GH_TOKEN": "ghp",
            "GITHUB_TOKEN": "ghp",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "A_KEY_INVENTED_NEXT_YEAR": "secret",
            "HERMES_OPENREADY_TIMEOUT_SECONDS": "5",
            "PYTHONPATH": "/somewhere",
        }
    )
    assert seen == {"PATH": "/usr/bin"}


def test_runner_defaults_to_the_minimal_environment(tmp_path, monkeypatch) -> None:
    """The default path is the safe one: no caller has to opt in."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("GH_TOKEN", "ghp-secret")
    monkeypatch.setenv("OPENREADY_MARKER", "kept")
    code = "import json,os;print(json.dumps(dict(os.environ)))"
    result = runner.run([sys.executable, "-c", code], timeout=60, cwd=str(tmp_path))
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert "ANTHROPIC_API_KEY" not in seen
    assert "GH_TOKEN" not in seen
    assert seen.get("OPENREADY_MARKER") == "kept"
