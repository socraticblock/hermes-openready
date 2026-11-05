"""Settings resolution tests: precedence, normalisation, and reported problems."""

from __future__ import annotations

from pathlib import Path

import pytest

from openready_plugin import settings


@pytest.fixture(autouse=True)
def no_host_config(monkeypatch):
    monkeypatch.setattr(settings, "_config_settings", lambda: {})
    for key in settings.DEFAULTS:
        monkeypatch.delenv(settings.ENV_PREFIX + key.upper(), raising=False)


def test_defaults_are_problem_free():
    conf = settings.load()
    assert conf.problems == ()
    assert conf.destination == ""
    assert conf.guard_enabled is True


def test_env_overrides_config(monkeypatch):
    monkeypatch.setattr(settings, "_config_settings", lambda: {"licence": "Apache-2.0"})
    monkeypatch.setenv("HERMES_OPENREADY_LICENCE", "BSD-3-Clause")
    assert settings.load().licence == "BSD-3-Clause"


def test_config_used_when_env_absent(monkeypatch):
    monkeypatch.setattr(settings, "_config_settings", lambda: {"timeout_seconds": 45})
    assert settings.load().timeout_seconds == 45


def test_malformed_values_are_reported_not_coerced(monkeypatch):
    monkeypatch.setenv("HERMES_OPENREADY_TIMEOUT_SECONDS", "soon")
    monkeypatch.setenv("HERMES_OPENREADY_GUARD_ENABLED", "maybe")
    monkeypatch.setenv("HERMES_OPENREADY_LICENCE", "   ")
    conf = settings.load()
    assert conf.timeout_seconds == settings.DEFAULTS["timeout_seconds"]
    assert conf.guard_enabled is settings.DEFAULTS["guard_enabled"]
    assert conf.licence == settings.DEFAULTS["licence"]
    joined = " ".join(conf.problems)
    assert "timeout_seconds" in joined and "guard_enabled" in joined and "licence" in joined


def test_non_positive_timeout_is_reported(monkeypatch):
    monkeypatch.setenv("HERMES_OPENREADY_APPROVAL_TIMEOUT_SECONDS", "0")
    conf = settings.load()
    assert conf.approval_timeout_seconds == settings.DEFAULTS["approval_timeout_seconds"]
    assert "not positive" in " ".join(conf.problems)


def test_boolean_forms(monkeypatch):
    monkeypatch.setenv("HERMES_OPENREADY_GUARD_ENABLED", "off")
    assert settings.load().guard_enabled is False
    monkeypatch.setenv("HERMES_OPENREADY_GUARD_ENABLED", "YES")
    assert settings.load().guard_enabled is True


def test_candidate_path_resolution(tmp_path):
    conf = settings.load()
    assert conf.candidate_path(tmp_path) == tmp_path / ".openready-candidate"
    absolute = settings.Settings(candidate_dir="/var/tmp/cand")
    assert absolute.candidate_path(tmp_path) == Path("/var/tmp/cand")


def test_host_config_is_ignored_when_absent(monkeypatch):
    # _config_settings must never raise when hermes_cli is missing or the key is absent.
    monkeypatch.undo()
    conf = settings.load()
    assert conf.problems == ()
