"""The environment a child process is allowed to see.

A Hermes host keeps provider keys, cloud credentials and tokens in its own
environment. This plugin shells out to a separately installed program, so
passing ``os.environ`` through — or inheriting it by default — hands every one
of those secrets to code the plugin did not write. The rule here is an
allowlist, not a denylist: a credential added to Hermes tomorrow is withheld
without anybody editing this file.

What is kept is what a Python child needs to start and import its package:
``PATH`` (find the interpreter), ``HOME`` (config and caches), ``LANG``/``LC_*``
(locale, so output decodes predictably), ``VIRTUAL_ENV`` (the interpreter that
can import ``openready``), ``TMPDIR``/``TMP``/``TEMP`` (temporary directory) and
``OPENREADY_*`` (variables Open-Ready itself reads).

GitHub tokens are absent from the allowlist on purpose, twice over: the approval
check must never be able to publish what it just judged, and a denial here does
not depend on remembering to strip each name.
"""

from __future__ import annotations

import os
from typing import Mapping

#: Exact names a child needs. Anything not listed, and not matched by a prefix
#: below, is withheld.
PASSTHROUGH = (
    "PATH",
    "HOME",
    "LANG",
    "VIRTUAL_ENV",
    "TMPDIR",
    "TMP",
    "TEMP",
)

#: Prefixes a child may read: locale categories and Open-Ready's own settings.
#: ``HERMES_OPENREADY_*`` is deliberately absent — those configure the plugin in
#: the parent, and the CLI does not read them.
PASSTHROUGH_PREFIXES = (
    "LC_",
    "OPENREADY_",
)


def minimal(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the environment a child process gets.

    *environ* defaults to the current process environment. Values are copied
    verbatim; nothing is invented, so a missing ``HOME`` stays missing and the
    child fails the way it would have failed anyway.
    """
    source: Mapping[str, str] = os.environ if environ is None else environ
    env: dict[str, str] = {}
    for key, value in source.items():
        if key in PASSTHROUGH or key.startswith(PASSTHROUGH_PREFIXES):
            env[key] = value
    return env
