"""Settings resolution for the Open-Ready adapter.

Precedence, highest first:

1. ``HERMES_OPENREADY_*`` environment variables (host-level override),
2. ``plugins.entries.hermes-openready.settings`` in the active Hermes config,
3. the defaults below.

Every value is normalised here and nowhere else, so the rest of the package can
assume types. A malformed value is *reported* through :attr:`Settings.problems`
rather than silently coerced: a guard whose configuration is wrong must say so.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PLUGIN_ID = "hermes-openready"
ENV_PREFIX = "HERMES_OPENREADY_"

DEFAULTS: dict[str, Any] = {
    "openready_bin": "openready",
    "openready_python": "python3",
    "timeout_seconds": 120,
    "approval_timeout_seconds": 20,
    "candidate_dir": ".openready-candidate",
    "licence": "MIT",
    "destination": "",
    "guard_enabled": True,
}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    openready_bin: str = DEFAULTS["openready_bin"]
    openready_python: str = DEFAULTS["openready_python"]
    timeout_seconds: int = DEFAULTS["timeout_seconds"]
    approval_timeout_seconds: int = DEFAULTS["approval_timeout_seconds"]
    candidate_dir: str = DEFAULTS["candidate_dir"]
    licence: str = DEFAULTS["licence"]
    destination: str = DEFAULTS["destination"]
    guard_enabled: bool = DEFAULTS["guard_enabled"]
    problems: tuple[str, ...] = field(default_factory=tuple)

    def candidate_path(self, repo: Path) -> Path:
        candidate = Path(self.candidate_dir).expanduser()
        return candidate if candidate.is_absolute() else (repo / candidate)


def _config_settings() -> dict[str, Any]:
    """Read the plugin's settings block from Hermes, if this process has one.

    Mirrors the host's own lookup (``plugins.entries.<id>.settings``, with the
    legacy ``config`` subtree as a fallback) so the adapter sees exactly what
    ``ctx.get_config`` would return. Imported lazily and defensively: the plugin
    must stay importable in a bare interpreter (tests, the validation probe)
    where ``hermes_cli`` is absent.
    """
    try:
        from hermes_cli.config import load_config_readonly  # type: ignore
    except Exception:
        try:
            from hermes_cli.config import load_config as load_config_readonly  # type: ignore
        except Exception:
            return {}
    try:
        config = load_config_readonly() or {}
    except Exception:
        return {}
    plugins = config.get("plugins") if isinstance(config, dict) else None
    entries = plugins.get("entries") if isinstance(plugins, dict) else None
    entry = entries.get(PLUGIN_ID) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {}
    for key in ("settings", "config"):
        block = entry.get(key)
        if isinstance(block, dict) and block:
            return block
    return {}


def _env_settings() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in DEFAULTS:
        raw = os.environ.get(ENV_PREFIX + key.upper())
        if raw is not None and raw != "":
            out[key] = raw
    return out


def _as_int(key: str, value: Any, problems: list[str]) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        problems.append(f"{key}: {value!r} is not an integer; using default {DEFAULTS[key]}")
        return None
    if parsed <= 0:
        problems.append(f"{key}: {parsed} is not positive; using default {DEFAULTS[key]}")
        return None
    return parsed


def _as_bool(key: str, value: Any, problems: list[str]) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    problems.append(f"{key}: {value!r} is not a boolean; using default {DEFAULTS[key]}")
    return None


def _as_text(key: str, value: Any, problems: list[str], *, allow_empty: bool = False) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text or allow_empty:
            return text
    problems.append(f"{key}: {value!r} is not a non-empty string; using default {DEFAULTS[key]!r}")
    return None


def load() -> Settings:
    """Resolve settings once per call. Cheap: no subprocess, no network."""
    merged: dict[str, Any] = dict(DEFAULTS)
    merged.update(_config_settings())
    merged.update(_env_settings())

    problems: list[str] = []
    values: dict[str, Any] = {}
    for key, default in DEFAULTS.items():
        raw = merged.get(key, default)
        if isinstance(default, bool):
            parsed = _as_bool(key, raw, problems)
            values[key] = default if parsed is None else parsed
        elif isinstance(default, int):
            parsed_int = _as_int(key, raw, problems)
            values[key] = default if parsed_int is None else parsed_int
        else:
            parsed_text = _as_text(key, raw, problems, allow_empty=(key == "destination"))
            values[key] = default if parsed_text is None else parsed_text

    return Settings(problems=tuple(problems), **values)
