"""A `server/discover` that lands mid-warm-up waits for it, as `tools/list` does (#2101).

The front door warms its upstreams on a thread, so for a moment after the
gateway answers `/health/live` nothing is projected yet. `tools/list` waits that
moment out (#1231); `server/discover` did not, and answered an empty catalogue
that the caller's `tools/list` a moment later did not. Live tier 0 caught it on
loaded runners, whenever the discover test was the first request a fresh
gateway saw.

Discover promises the caller's `tools/list` surface, so it waits on the same
condition, through the same helper -- and does not wait where the listing does
not.
"""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    ToolProjectionRegistry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import ToolAccessResolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server import catalogue_warmup, server_discover

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


@pytest.fixture
def resolver() -> ToolAccessResolver:
    r = ToolAccessResolver()
    r.set_topology_mode("front_door")
    return r


def _populate(registry: ToolProjectionRegistry) -> None:
    registry.build_from_tools(
        "math",
        [ToolSchema(name=t, description=t, input_schema={"type": "object"}) for t in ("add", "subtract")],
    )


async def _discover(registry, resolver, tenant_id: str | None) -> set[str]:
    request = Mock()
    request.method = "GET"
    caller = CallerIdentity(
        user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant_id
    )
    token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        with patch(_PROJ_PATH, return_value=registry), patch(_RESOLVER_PATH, return_value=resolver):
            response = await server_discover.server_discover_handler(request)
    finally:
        identity_context_var.reset(token)
    return {tool["name"] for tool in json.loads(bytes(response.body).decode())["tools"]}


def _land_the_warm_up_after(registry: ToolProjectionRegistry, delay_s: float) -> None:
    """Project the upstream and finish the warm-up from another thread, as the real warm-up does."""

    def land() -> None:
        _populate(registry)
        catalogue_warmup.warmup_finished()

    threading.Timer(delay_s, land).start()


@pytest.mark.asyncio
async def test_a_discover_mid_warm_up_answers_the_catalogue_the_warm_up_lands(registry, resolver) -> None:
    # Once before the warm-up, so the first build's lazy imports are not what the timer races.
    assert await _discover(registry, resolver, "tenant:a") == set()
    catalogue_warmup.warmup_started()
    _land_the_warm_up_after(registry, 0.3)

    started = time.monotonic()
    assert {"add", "subtract"} <= await _discover(registry, resolver, "tenant:a")
    # Returned on the warm-up, not on the deadline.
    assert time.monotonic() - started < catalogue_warmup.WARMUP_LIST_WAIT_S


@pytest.mark.asyncio
async def test_a_caller_without_identity_is_answered_at_once(registry, resolver) -> None:
    """A fail-closed deny stays instant, on discover as on the listing."""
    catalogue_warmup.warmup_started()

    started = time.monotonic()
    assert await _discover(registry, resolver, None) == set()
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_a_catalogue_policy_emptied_is_answered_at_once(registry, resolver) -> None:
    """Something is projected and this tenant may see none of it: `[]` is the truth, not a warm-up."""
    _populate(registry)
    registry.withdraw("math", "add", tenant_id="tenant:a")
    registry.withdraw("math", "subtract", tenant_id="tenant:a")
    catalogue_warmup.warmup_started()

    started = time.monotonic()
    assert not {"add", "subtract"} & await _discover(registry, resolver, "tenant:a")
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_no_warm_up_means_no_wait(registry, resolver) -> None:
    """An `egress` gateway never warms, and a finished warm-up is not waited on again."""
    started = time.monotonic()
    assert not {"add", "subtract"} & await _discover(registry, resolver, "tenant:a")
    assert time.monotonic() - started < 1.0
