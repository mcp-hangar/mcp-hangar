"""The front door's warm-up counts as running from before its thread starts (#2119).

`tools/list` and `server/discover` wait out the boot warm-up when nothing is
projected yet (#1231, #2101), but only while `is_warming()` says so. The warm-up
marked itself from inside its thread, so for the moment between the thread
being created and its first statement, a listing read "not warming" and
answered only the `hangar_*` tools at once. `start_front_door_warm_up` now marks
it before the thread exists, which is before `serve` goes on to accept a
request.

The thread here is held at its start, which is the loaded runner's window made
as wide as the test wants.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import catalogue_warmup, server_discover
from mcp_hangar.server import lifecycle

_PROJ_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_projection_registry"
_RESOLVER_PATH = "mcp_hangar.fastmcp_server.flat_tool_projection.get_tool_access_resolver"


@pytest.fixture(autouse=True)
def _clean_state():
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


def _topology(mode: str) -> ToolAccessResolver:
    resolver = get_tool_access_resolver()
    resolver.set_topology_mode(mode)
    return resolver


class _HeldThread(threading.Thread):
    """A thread that waits at its start until the test releases it."""

    release = threading.Event()

    def run(self) -> None:
        self.release.wait(timeout=10)
        super().run()


@pytest.fixture
def held():
    _HeldThread.release = threading.Event()
    with patch.object(lifecycle.threading, "Thread", _HeldThread):
        yield _HeldThread.release
    _HeldThread.release.set()


def _runtime(registry: ToolProjectionRegistry, server_ids: list[str]) -> Any:
    """A fleet whose start projects the server's tools, as `McpServerStarted` does."""

    def send(command: Any) -> None:
        registry.build_from_tools(
            command.mcp_server_id,
            [ToolSchema(name=t, description=t, input_schema={"type": "object"}) for t in ("add", "subtract")],
        )

    repository = Mock()
    repository.get_all_ids.return_value = server_ids
    repository.get.return_value = Mock(state=Mock(value="cold"))
    return type("R", (), {"repository": repository, "command_bus": Mock(send=Mock(side_effect=send))})()


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


@pytest.mark.asyncio
async def test_a_listing_before_the_thread_runs_waits_for_the_full_catalogue(registry, held) -> None:
    resolver = _topology("front_door")
    # Once before the warm-up, so the first build's lazy imports are not what the timer races.
    assert await _discover(registry, resolver) == set()

    thread = lifecycle.start_front_door_warm_up(_runtime(registry, ["math"]), Mock())
    assert catalogue_warmup.is_warming() is True, "marked before the thread ran its first statement"

    threading.Timer(0.3, held.set).start()
    started = time.monotonic()
    assert {"add", "subtract"} <= await _discover(registry, resolver)
    # Returned on the warm-up, not on the deadline.
    assert time.monotonic() - started < catalogue_warmup.WARMUP_LIST_WAIT_S

    thread.join(timeout=5)
    assert catalogue_warmup.is_warming() is False


def test_a_thread_that_does_not_start_leaves_nothing_warming(registry) -> None:
    _topology("front_door")

    class _Unstartable(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    with patch.object(lifecycle.threading, "Thread", _Unstartable), pytest.raises(RuntimeError):
        lifecycle.start_front_door_warm_up(_runtime(registry, ["math"]), Mock())

    assert catalogue_warmup.is_warming() is False


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_warm_up_that_raises_on_its_thread_still_ends(registry) -> None:
    _topology("front_door")
    retry = Mock()

    with patch.object(lifecycle, "warm_the_front_door_catalogue", side_effect=RuntimeError("boom")):
        thread = lifecycle.start_front_door_warm_up(_runtime(registry, ["math"]), retry)
        thread.join(timeout=5)

    assert catalogue_warmup.is_warming() is False


@pytest.mark.parametrize(
    ("mode", "server_ids"),
    [("egress", ["math"]), ("front_door", [])],
    ids=["egress", "front-door-with-no-servers"],
)
def test_where_no_warm_up_runs_nothing_is_marked(registry, held, mode: str, server_ids: list[str]) -> None:
    _topology(mode)
    retry = Mock()

    thread = lifecycle.start_front_door_warm_up(_runtime(registry, server_ids), retry)
    assert catalogue_warmup.is_warming() is False, "held at its start, the thread has not run"

    held.set()
    thread.join(timeout=5)
    assert catalogue_warmup.is_warming() is False
    retry.run.assert_called_once()
