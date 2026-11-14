"""The front door as it was before ``tool_projection.flat`` (#1372), declared explicitly.

A front door projects only the tools each server and group names in
``tool_projection.flat``, and refuses to start when one names none. The
harnesses here test other controls on a front door that projected every tool,
so each names its catalogue: every tool a scope serves that the scope's own
``tools:`` leaves visible, which is what the loader accepts.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The tools `tests/mock_provider.py` serves.
MOCK_PROVIDER_TOOLS = ("add", "subtract", "multiply", "divide", "power", "echo")


def name_every_tool(config: dict[str, Any], catalogue: Mapping[str, Iterable[str]]) -> dict[str, Any]:
    """Set ``tool_projection.flat`` on every scope in *config* from *catalogue*, and return *config*.

    Args:
        config: A full configuration; its ``mcp_servers`` are edited in place.
        catalogue: The tools each scope serves, by scope id. A scope absent
            from it names none.
    """
    from mcp_hangar.server.config import _not_visible

    for scope_id, spec in config["mcp_servers"].items():
        names = frozenset(catalogue.get(scope_id, ()))
        spec.setdefault("tool_projection", {})["flat"] = sorted(names - _not_visible(scope_id, spec, names))
    return config
