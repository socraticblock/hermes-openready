"""Registration wiring.

Kept in the package rather than the repository root so the root ``__init__.py``
can use an absolute import: tooling that imports the plugin directory as a
top-level module (pytest, ad-hoc probes) then behaves the same as Hermes loading
it as a package.
"""

from __future__ import annotations

from . import hook, tools


def register(ctx) -> None:
    """Declare the tools and the publish guard. No I/O, no state, no side effects."""
    for name, schema, handler in tools.TOOLS:
        ctx.register_tool(
            name=name,
            toolset="openready",
            schema=schema,
            handler=handler,
            check_fn=tools.openready_available,
            description=schema["description"],
            emoji=tools.EMOJI.get(name, ""),
        )
    ctx.register_hook("pre_tool_call", hook.pre_tool_call)
