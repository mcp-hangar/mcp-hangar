"""A tool the pins do not admit is refused on its first call and not described on REST (#2033).

`tool_projection.new_tools: withhold` on a server that has pins withholds every
tool outside them. Two ways round it were left open:

* The first ``hangar_call`` of such a tool on a COLD server ran it. The
  withdrawal gate read "not in the catalogue" as "do not block", and the
  catalogue is built by the cold start, later in the pipeline. The second call
  was refused.
* ``GET /api/mcp_servers/{id}/tools``, ``GET /api/tools/`` and
  ``GET /api/mcp_servers/{id}`` listed it, and an all-tenants ``withdrawn:``
  tool, with the full description and no mark.

The gateway is the served one over a real upstream behind a real ``McpServer``
(`_front_door_harness`), and the REST routes are the router ``serve --http``
mounts, over that same fleet.

Naming: neutral placeholders only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.queries import GetMcpServerQuery, GetMcpServerToolsQuery, ListMcpServersQuery
from mcp_hangar.application.queries.handlers import (
    GetMcpServerHandler,
    GetMcpServerToolsHandler,
    ListMcpServersHandler,
)
from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.context import get_context

from ._front_door_harness import SERVER, TENANT_A, FrontDoor, front_door, jsonrpc

BASELINE = "read_item"
ADDED = "write_item"
RETIRED = "old_item"
COLD = "store-cold"


def _digest_of(tool: str) -> ToolDigest:
    """*tool*'s digest as the upstream serves it, read off the warm server's catalogue."""
    (projection,) = [p for p in get_tool_projection_registry().list_for_server(SERVER) if p.tool == tool]
    return ToolDigest(tool_name=tool, sha256=projection.digest.sha256)


def _pin(server: str, *tools: str, policy: str | None) -> None:
    registry = get_tool_projection_registry()
    for tool in tools:
        registry.set_config_pin(server, tool, None, _digest_of(tool))
    if policy is not None:
        registry.set_new_tools_policy(server, policy)


def _call(door: FrontDoor, server: str, tool: str) -> str | None:
    calls = [{"mcp_server": server, "tool": tool, "arguments": {"x": "1"}}]
    payload = jsonrpc(door.call(TENANT_A, "hangar_call", {"calls": calls}))
    assert "structuredContent" in payload.get("result", {}), payload
    (result,) = payload["result"]["structuredContent"]["results"]
    return None if result["success"] else str(result["error_type"])


class TestTheFirstCallOnAColdServer:
    def test_is_refused_like_the_second_and_starts_nothing(self) -> None:
        with front_door((BASELINE, ADDED), topology="egress", also=(COLD,)) as door:
            _pin(COLD, BASELINE, policy="withhold")

            assert _call(door, COLD, ADDED) == "ToolWithdrawnError"
            assert _call(door, COLD, ADDED) == "ToolWithdrawnError"
            assert door.upstream.called == []
            assert get_context().repository.get(COLD).state.value == "cold"

    def test_of_a_pinned_tool_still_runs_after_its_pin_is_checked(self) -> None:
        with front_door((BASELINE, ADDED), topology="egress", also=(COLD,)) as door:
            _pin(COLD, BASELINE, policy="withhold")

            assert _call(door, COLD, BASELINE) is None
            assert door.upstream.called == [BASELINE]

    @pytest.mark.parametrize("policy", [None, "allow"])
    def test_without_withhold_runs_as_before(self, policy: str | None) -> None:
        with front_door((BASELINE, ADDED), topology="egress", also=(COLD,)) as door:
            _pin(COLD, BASELINE, policy=policy)

            assert _call(door, COLD, ADDED) is None
            assert door.upstream.called == [ADDED]


@contextmanager
def _rest() -> Iterator[TestClient]:
    """The REST router ``serve --http`` mounts at ``/api``, auth off, over the harness's fleet."""
    ctx = get_context()
    # The harness registers only the queries the invoke path sends, on a process-wide bus.
    handlers = {
        ListMcpServersQuery: ListMcpServersHandler(ctx.repository),
        GetMcpServerQuery: GetMcpServerHandler(ctx.repository),
        GetMcpServerToolsQuery: GetMcpServerToolsHandler(ctx.repository),
    }
    for query, handler in handlers.items():
        ctx.query_bus.unregister(query)
        ctx.query_bus.register(query, handler)
    try:
        with TestClient(create_api_router()) as client:
            yield client
    finally:
        for query in handlers:
            ctx.query_bus.unregister(query)


def _rest_listings(client: TestClient) -> dict[str, dict[str, dict[str, Any]]]:
    """Each REST listing of SERVER's tools, keyed by route, then by tool name."""
    by_route = {
        "/mcp_servers/{id}/tools": client.get(f"/mcp_servers/{SERVER}/tools").json()["tools"],
        "/mcp_servers/{id}": client.get(f"/mcp_servers/{SERVER}").json()["tools"],
    }
    flat = [
        {**entry, "name": entry["tool_name"]}
        for entry in client.get("/tools/").json()["tools"]
        if entry["mcp_server_id"] == SERVER
    ]
    by_route["/tools/"] = flat
    return {route: {entry["name"]: entry for entry in tools} for route, tools in by_route.items()}


class TestRest:
    def test_marks_an_unadmitted_and_a_withdrawn_tool_without_their_definitions(self) -> None:
        with front_door((BASELINE, ADDED, RETIRED), topology="egress"):
            _pin(SERVER, BASELINE, RETIRED, policy="withhold")
            get_tool_projection_registry().set_config_withdrawal(SERVER, RETIRED)

            with _rest() as client:
                listings = _rest_listings(client)

            for route, tools in listings.items():
                assert set(tools) == {BASELINE, ADDED, RETIRED}, route
                assert "withheld" not in tools[BASELINE], route
                for name in (ADDED, RETIRED):
                    entry = tools[name]
                    assert entry["withheld"] == "withdrawn", (route, entry)
                    assert not {"description", "inputSchema", "input_schema"} & set(entry), (route, entry)

    @pytest.mark.parametrize("policy", [None, "allow"])
    def test_lists_every_tool_whole_without_withhold_or_withdrawn(self, policy: str | None) -> None:
        with front_door((BASELINE, ADDED), topology="egress"):
            _pin(SERVER, BASELINE, policy=policy)

            with _rest() as client:
                listings = _rest_listings(client)

            for route, tools in listings.items():
                assert set(tools) == {BASELINE, ADDED}, route
                for entry in tools.values():
                    assert "withheld" not in entry, (route, entry)
                    assert {"inputSchema", "input_schema"} & set(entry), (route, entry)
