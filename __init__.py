"""Hermes plugin entry point for Open-Ready.

Two loaders import this file and they disagree about what it is:

* Hermes imports the directory as a package (the loader and ``hermes plugins
  validate``'s capability probe both set ``submodule_search_locations``), so the
  relative import is the real one.
* Test tooling imports a bare ``__init__.py`` as a top-level module, where a
  relative import cannot resolve.

The fallback exists only for the second case and is exercised by the test suite;
it is not a second code path in production. Nothing else happens at import time:
the validator records what ``register()`` declares in a scratch subprocess, so
import-time I/O would make admission depend on the host.
"""

from __future__ import annotations

try:  # Hermes: directory loaded as a package
    from .openready_plugin.plugin import register
except ImportError:  # tooling: imported as a top-level module
    from openready_plugin.plugin import register  # type: ignore[no-redef]

__all__ = ["register"]
