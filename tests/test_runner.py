"""Runner tests: the four host failures that must become data, not exceptions."""

from __future__ import annotations

import sys

from openready_plugin.runner import run


def test_missing_binary_is_reported():
    result = run(["/nonexistent/openready-binary"], timeout=5)
    assert result.missing is True
    assert result.returncode == 127
    assert "not found" in result.detail


def test_timeout_is_reported_and_bounded():
    result = run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
    assert result.timed_out is True
    assert result.returncode == 124


def test_timeout_kills_descendants_that_hold_the_pipe():
    """A grandchild inheriting stdout must not extend the deadline."""
    import time

    script = (
        "import subprocess, sys, time;"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
        "time.sleep(30)"
    )
    started = time.monotonic()
    result = run([sys.executable, "-c", script], timeout=0.5)
    elapsed = time.monotonic() - started
    assert result.timed_out is True
    assert elapsed < 10, f"timeout leaked: {elapsed:.1f}s"


def test_json_parses_and_non_json_returns_none():
    good = run([sys.executable, "-c", "print('{\"a\": 1}')"], timeout=10)
    assert good.json() == {"a": 1}
    bad = run([sys.executable, "-c", "print('not json')"], timeout=10)
    assert bad.json() is None


def test_non_zero_exit_is_data():
    result = run([sys.executable, "-c", "import sys; sys.exit(2)"], timeout=10)
    assert result.returncode == 2
    assert result.ok is False


def test_output_is_capped():
    result = run([sys.executable, "-c", "print('x' * 300000)"], timeout=20)
    assert len(result.stdout) <= 200_000


def test_empty_argv_is_refused():
    result = run([], timeout=5)
    assert result.missing is True
