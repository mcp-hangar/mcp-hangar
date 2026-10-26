"""A warm-up that never finishes stops holding empty listings after its bound (#2122).

`tools/list` and `server/discover` wait, up to 5 s, for the boot warm-up when an
identified caller would otherwise get an empty catalogue (#1231, #2101). A
warm-up that hung, on an upstream handshake that never returns, kept
`is_warming()` True for the life of the process, so every such listing waited
the full 5 s, every time. The warming state now ends at a bound derived from the
servers being warmed, with a WARNING naming the ones still warming. The warm-up
itself goes on: a server it starts late is projected and announced as before.

The bound is injected short here; the hung upstream is a start held on an event.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from structlog.testing import capture_logs

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import catalogue_warmup, server_discover, tool_list_changed
from mcp_hangar.server import lifecycle

_PROJ_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry"
_RESOLVER_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver"
_BOUND_S = 0.2
_BOUND_PASSED = "front_door_warmup_bound_passed"


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    catalogue_warmup.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    catalogue_warmup.reset()
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def registry() -> ToolProjectionRegistry:
    return ToolProjectionRegistry()


@pytest.fixture
def resolver() -> ToolAccessResolver:
    resolver = get_tool_access_resolver()
    resolver.set_topology_mode("front_door")
    return resolver


@pytest.fixture
def short_bound() -> Iterator[None]:
    with patch.object(lifecycle, "warm_up_bound_s", return_value=_BOUND_S):
        yield


@pytest.fixture
def hung() -> Iterator[threading.Event]:
    """Released by the test, or at teardown, so no thread outlives it."""
    release = threading.Event()
    yield release
    release.set()


def _runtime(registry: ToolProjectionRegistry, server_ids: list[str], hold: threading.Event | None = None) -> Any:
    """A fleet whose start projects the server's tools, as `McpServerStarted` does, once *hold* is set."""

    def send(command: Any) -> None:
        if hold is not None:
            hold.wait(timeout=10)
        registry.build_from_tools(
            command.mcp_server_id,
            [ToolSchema(name=t, description=t, input_schema={"type": "object"}) for t in ("add", "subtract")],
        )

    repository = Mock()
    repository.get_all_ids.return_value = server_ids
    repository.get.return_value = Mock(state=Mock(value="cold"))
    return SimpleNamespace(repository=repository, command_bus=Mock(send=Mock(side_effect=send)))


async def _discover(registry: ToolProjectionRegistry, resolver: ToolAccessResolver) -> set[str]:
    request = Mock()
    request.method = "GET"
    caller = CallerIdentity(
        user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id="tenant:a"
    )
    token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        with patch(_PROJ_PATH, return_value=registry), patch(_RESOLVER_PATH, return_value=resolver):
            response = await server_discover.server_discover_handler(request)
    finally:
        identity_context_var.reset(token)
    return {tool["name"] for tool in json.loads(bytes(response.body).decode())["tools"]}


def _until(condition: Any, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


@pytest.mark.asyncio
async def test_a_warm_up_that_never_returns_stops_holding_listings_after_its_bound(
    registry, resolver, short_bound, hung
) -> None:
    with capture_logs() as logs:
        thread = lifecycle.start_front_door_warm_up(_runtime(registry, ["math", "search"], hold=hung), Mock())
        assert catalogue_warmup.is_warming() is True

        assert _until(lambda: not catalogue_warmup.is_warming()), "the warming state outlived its bound"

        started = time.monotonic()
        assert await _discover(registry, resolver) == set()
        # Answered at once, not after the listing's 5 s wait.
        assert time.monotonic() - started < 1.0
        assert thread.is_alive(), "the warm-up itself goes on"

    passed = [entry for entry in logs if entry["event"] == _BOUND_PASSED]
    assert len(passed) == 1, logs
    assert passed[0]["log_level"] == "warning"
    assert passed[0]["still_warming"] == ["math", "search"]
    assert passed[0]["bound_s"] == _BOUND_S


@pytest.mark.asyncio
async def test_a_warm_up_that_finishes_after_its_bound_still_projects_and_announces(
    registry, resolver, short_bound, hung
) -> None:
    with patch.object(tool_list_changed, "flush_now") as flush_now:
        thread = lifecycle.start_front_door_warm_up(_runtime(registry, ["math"], hold=hung), Mock())
        assert _until(lambda: not catalogue_warmup.is_warming())
        flush_now.assert_not_called()

        hung.set()
        thread.join(timeout=5)

        assert not thread.is_alive()
        flush_now.assert_called()
    assert {"add", "subtract"} <= await _discover(registry, resolver)
    assert catalogue_warmup.is_warming() is False


@pytest.mark.asyncio
async def test_a_warm_up_that_finishes_in_time_is_unchanged(registry, resolver, hung) -> None:
    # A bound the warm-up does not race, even on a loaded runner.
    bound_s = 1.0
    # Once before the warm-up, so the first build's lazy imports are not what the timer races.
    assert await _discover(registry, resolver) == set()

    with capture_logs() as logs, patch.object(lifecycle, "warm_up_bound_s", return_value=bound_s):
        thread = lifecycle.start_front_door_warm_up(_runtime(registry, ["math"], hold=hung), Mock())
        threading.Timer(0.05, hung.set).start()

        # The listing waits for the warm-up and gets the full catalogue.
        assert {"add", "subtract"} <= await _discover(registry, resolver)
        thread.join(timeout=5)
        # Past the bound: a warm-up that finished in time is not reported.
        time.sleep(bound_s * 1.2)

    assert catalogue_warmup.is_warming() is False
    assert [entry for entry in logs if entry["event"] == _BOUND_PASSED] == []


def test_a_warm_up_past_its_bound_is_not_marked_again_by_its_own_thread() -> None:
    """The thread's own `warmup_started` must not start the wait over, unbounded."""
    catalogue_warmup.warmup_started(0.05, ["math"])
    assert _until(lambda: not catalogue_warmup.is_warming())

    catalogue_warmup.warmup_started()

    assert catalogue_warmup.is_warming() is False


def test_a_finished_warm_up_can_be_marked_again() -> None:
    catalogue_warmup.warmup_started(0.05, ["math"])
    catalogue_warmup.warmup_finished()

    catalogue_warmup.warmup_started()

    assert catalogue_warmup.is_warming() is True


class TestTheBound:
    """Derived from the servers being warmed, one at a time, so a sum."""

    @staticmethod
    def _runtime(servers: dict[str, Any]) -> Any:
        return SimpleNamespace(repository=SimpleNamespace(get=servers.get))

    def test_each_stdio_server_adds_its_start_budget(self) -> None:
        servers = {
            "math": McpServer(mcp_server_id="math", mode="subprocess", command=["unused"]),
            "search": McpServer(mcp_server_id="search", mode="docker", image="example/search:1"),
        }

        assert lifecycle.warm_up_bound_s(self._runtime(servers), ["math", "search"]) == 30.0 + 30.0 + 30.0

    def test_a_remote_server_adds_its_configured_http_timeouts(self) -> None:
        remote = McpServer(
            mcp_server_id="api",
            mode="remote",
            endpoint="https://upstream.example/mcp",
            http={"connect_timeout": 5, "read_timeout": 60},
        )

        assert lifecycle.warm_up_bound_s(self._runtime({"api": remote}), ["api"]) == 30.0 + 2 * (5 + 60)

    def test_a_server_the_repository_does_not_hold_gets_the_floor(self) -> None:
        assert lifecycle.warm_up_bound_s(self._runtime({}), ["gone"]) == 30.0 + 30.0
