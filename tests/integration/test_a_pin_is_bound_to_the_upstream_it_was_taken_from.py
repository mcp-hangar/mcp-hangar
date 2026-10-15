"""A pin bound to an upstream does not hold for a server that points somewhere else (#1802).

A server's id is the name the operator gave it, not a property of the upstream.
So two servers serving a tool with an identical definition shared a pin by
copy, and renaming a server in the configuration carried its pins to whatever
the new block pointed at. A pin now carries the upstream identity it was taken
from (`tool_projection.upstream`, written by `mcp-hangar pin`), and a server
whose upstream differs does not match it.

The served app is the egress topology, so the call goes through `hangar_call`
and the digest gate, over one real upstream behind a real `McpServer`.

Naming: neutral placeholders only.
"""

from __future__ import annotations

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import DigestMismatchEvent
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.server.context import get_context

from ._front_door_harness import SERVER, TENANT_A, FrontDoor, front_door, jsonrpc

READ = "read_item"
ELSEWHERE = "remote:http://other.example:8443"


def _call(door: FrontDoor) -> str | None:
    """Call READ through `hangar_call`; the call's `error_type`, None when it was served."""
    calls = [{"mcp_server": SERVER, "tool": READ, "arguments": {"x": "1"}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    (result,) = payload["result"]["structuredContent"]["results"]
    return None if result["success"] else str(result["error_type"])


def _pin(upstream: str | None) -> None:
    """Pin READ for all tenants to the digest the upstream serves now, bound to *upstream*."""
    registry = get_tool_projection_registry()
    (projection,) = [p for p in registry.list_for_server(SERVER) if p.tool == READ]
    digest = ToolDigest(tool_name=READ, sha256=projection.digest.sha256, upstream=upstream)
    registry.set_config_pin(SERVER, READ, None, digest)


def test_a_pin_bound_to_this_servers_upstream_holds() -> None:
    with front_door((READ,), topology="egress") as door:
        identity = get_context().repository.get(SERVER).upstream_identity()
        _pin(identity)

        assert _call(door) is None


def test_the_same_definition_pinned_for_another_upstream_is_refused() -> None:
    """The copy: server B carries server A's pins, and points at a different upstream."""
    with front_door((READ,), topology="egress") as door:
        _pin(ELSEWHERE)

        assert _call(door) == "ToolDigestMismatchError"


def test_a_pin_that_never_named_an_upstream_checks_the_definition_only() -> None:
    with front_door((READ,), topology="egress") as door:
        _pin(None)

        assert _call(door) is None


def test_the_refusal_says_the_upstream_moved() -> None:
    with front_door((READ,), topology="egress") as door:
        seen: list[DigestMismatchEvent] = []
        get_context().runtime.event_bus.subscribe(DigestMismatchEvent, seen.append, kind=HandlerKind.EFFECT)
        identity = get_context().repository.get(SERVER).upstream_identity()
        _pin(ELSEWHERE)

        assert _call(door) == "ToolDigestMismatchError"

        assert [(e.expected_upstream, e.observed_upstream) for e in seen] == [(ELSEWHERE, identity)]
