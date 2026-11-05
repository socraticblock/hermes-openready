"""Open-Ready adapter for Hermes Agent.

Package layout:

``settings``  configuration resolution (env > config.yaml > defaults)
``runner``    bounded subprocess execution; never raises, never hangs
``publish``   pure command grammar and decision table
``evidence``  read-only owner-approval lookup via ``openready.publisher``
``tools``     tool schemas and handlers
``hook``      the ``pre_tool_call`` publish guard

The invariant the package exists to preserve: **no path in this package
publishes anything.** There is no push, no visibility change, no upload. The only
mutation any tool can cause is Open-Ready writing a candidate directory.
"""

from __future__ import annotations

__all__ = ["settings", "runner", "publish", "evidence", "tools", "hook"]
__version__ = "0.1.0"
