"""A projection is generated apart from the listing that serves it (#1367).

The 2026-09-10 diagnosis needed a live probe from outside to learn that Hangar
held 46 tools while a connected client saw none. Nothing could ask the gateway
what it would project without being a client. These tests pin the operation that
answers that question. They check that the listing serves it, that it changes
when the warm-up lands, and that asking for it costs nothing: it registers,
warms, starts, waits on and counts nothing.

The registry, the resolver, the warm-up state and the ``McpServerStarted``
handler that fills the registry are all real. The only stand-in is the
upstream start itself, because there is no upstream to shake hands with.

Naming: neutral placeholders only (server_a, server_b, read_item, tenant:a).
"""

from __future__ import annotations

import importlib
import threading
import time
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.application.event_handlers.tool_projection_handler import ToolProjectionPopulationHandler
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.events import McpServerStarted
from mcp_hangar.domain.model.tool_catalog import ToolCatalog, ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import catalogue_warmup, flat_tool_projection
from mcp_hangar.fastmcp_server.flat_tool_projection import Projection, generate_projection
from mcp_hangar.server import lifecycle

TENANT = "tenant:a"
OTHER_TENANT = "tenant:b"


def _schema(name: str, description: str | None = None) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=description or f"Does {name}",
        input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )


class _Fleet:
    """The fleet as the warm-up sees it: a repository and a command bus.

    Starting a server is the one stand-in. It ends where a real start ends, in
    the ``McpServerStarted`` handler that fills the projection registry.
    """

    def __init__(self, catalogue: dict[str, list[str]]) -> None:
        self._servers = {
            sid: SimpleNamespace(tools=ToolCatalog({t: _schema(t) for t in tools}), state=SimpleNamespace(value="cold"))
            for sid, tools in catalogue.items()
        }
        self.started: list[str] = []
        self.repository = self
        self.command_bus = self

    def get_all_ids(self) -> list[str]:
        return list(self._servers)

    def get(self, mcp_server_id: str) -> Any:
        return self._servers.get(mcp_server_id)

    def send(self, command: StartMcpServerCommand) -> None:
        self.started.append(command.mcp_server_id)
        server = self._servers[command.mcp_server_id]
        ToolProjectionPopulationHandler(self).handle(
            McpServerStarted(
                mcp_server_id=command.mcp_server_id,
                mode="subprocess",
                tools_count=server.tools.count(),
                startup_duration_ms=1.0,
            )
        )

    def rediscover(self, mcp_server_id: str, tools: list[ToolSchema]) -> None:
        """The server restarts and its handshake reports *tools*."""
        self._servers[mcp_server_id].tools = ToolCatalog({t.name: t for t in tools})
        self.send(StartMcpServerCommand(mcp_server_id=mcp_server_id))


@pytest.fixture(autouse=True)
def _clean_state():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()
    get_tool_access_resolver().set_topology_mode("front_door")
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()


def _names(projection: Projection) -> list[str]:
    return list(projection.tools)


def _count(kind: str) -> float:
    _, _, counts = prometheus_metrics.PROJECTED_TOOLS.collect()
    return next((s.value for s in counts if s.labels.get("kind") == kind), 0.0)


def _empty_total() -> float:
    return sum(s.value for s in prometheus_metrics.EMPTY_PROJECTION_TOTAL.collect())


def _listing_counters() -> tuple[float, float, float]:
    return _count("governed"), _count("management"), _empty_total()


def _registered_policies(resolver: Any) -> dict[str, dict[Any, Any]]:
    """Every policy the resolver holds. Its memo of computed answers is left out on purpose."""
    names = (
        "_mcp_server_policies",
        "_group_policies",
        "_member_policies",
        "_standalone_member_policies",
        "_member_mcp_server_mapping",
    )
    return {name: dict(getattr(resolver, name)) for name in names}


def _ctx(tenant_id: str) -> SimpleNamespace:
    """An SDK v2 request context carrying an authenticated principal and a tools/list body."""
    principal = Principal(id=PrincipalId("user:one"), type=PrincipalType.USER, tenant_id=tenant_id)
    request = SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=principal)),
        _body=b'{"jsonrpc":"2.0","method":"tools/list","params":{}}',
    )
    return SimpleNamespace(request=request)


def _list_handler(monkeypatch) -> Any:
    """The tools/list handler the front door registers, with no management surface."""
    monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: frozenset())
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers["tools/list"]


class TestTheWarmUpChangesTheProjection:
    def test_a_projection_generated_after_the_warm_up_differs_from_one_generated_before_it(self) -> None:
        """The diagnosed condition: the fleet warmed and the caller's projection must follow it."""
        fleet = _Fleet({"server_a": ["read_item", "get_item"]})

        before = generate_projection(TENANT)
        lifecycle.warm_the_front_door_catalogue(fleet)
        after = generate_projection(TENANT)

        assert fleet.started == ["server_a"]
        assert before.tenant_id == after.tenant_id == TENANT
        assert _names(before) == []
        assert sorted(_names(after)) == ["get_item", "read_item"]
        assert after != before, "the warm-up landed and the projection for the same identity did not change"

    def test_one_generated_mid_warm_up_is_answered_at_once_with_what_exists(self) -> None:
        """Inspection reads the state; it does not wait for the warm-up as a listing would."""
        fleet = _Fleet({"server_a": ["read_item"]})
        seen: list[tuple[bool, Projection, float]] = []
        start = fleet.send

        def inspect_then_start(command: StartMcpServerCommand) -> None:
            clock = time.monotonic()
            projection = generate_projection(TENANT)
            seen.append((catalogue_warmup.is_warming(), projection, time.monotonic() - clock))
            start(command)

        fleet.send = inspect_then_start  # type: ignore[method-assign]

        lifecycle.warm_the_front_door_catalogue(fleet)

        warming, during, elapsed = seen[0]
        assert warming is True, "the projection was meant to be generated while the warm-up was in flight"
        assert _names(during) == []
        assert elapsed < 1.0, "generating a projection waited on the warm-up"
        assert generate_projection(TENANT) != during


class TestGeneratingHasNoSideEffects:
    def test_generating_registers_warms_and_starts_nothing(self, monkeypatch) -> None:
        """Generation reads state. Every way it could change that state is watched here."""
        fleet = _Fleet({"server_a": ["read_item", "get_item"], "server_b": ["list_items"]})
        fleet.send(StartMcpServerCommand(mcp_server_id="server_a"))  # server_b stays cold
        resolver = get_tool_access_resolver()
        resolver.set_standalone_member_policy("server_a", TENANT, ToolAccessPolicy(deny_list=("get_item",)))

        # The two process-global ways to start an upstream.
        sends: list[Any] = []
        recorder = SimpleNamespace(send=sends.append)
        monkeypatch.setattr("mcp_hangar.infrastructure.command_bus.get_command_bus", lambda: recorder)
        # By module object: `mcp_hangar.server.bootstrap` is also a function, and
        # a dotted-string patch resolves to that instead of the package.
        monkeypatch.setattr(
            importlib.import_module("mcp_hangar.server.bootstrap.composition"),
            "get_runtime",
            lambda *a, **k: SimpleNamespace(command_bus=recorder),
        )

        registry = get_tool_projection_registry()
        catalogue = registry.all()
        policies = _registered_policies(resolver)

        for tenant in (TENANT, OTHER_TENANT, None):
            generate_projection(tenant)

        assert sends == [], "generating a projection sent a command"
        assert fleet.started == ["server_a"], "generating a projection started a cold server"
        assert registry.all() == catalogue, "generating a projection changed the catalogue"
        assert _registered_policies(resolver) == policies, "generating a projection registered a policy"
        # Private on purpose: `is_warming()` cannot tell "never started" from "finished".
        assert catalogue_warmup._in_flight is None, "generating a projection started a warm-up"

    def test_generating_is_not_counted_as_a_listing(self) -> None:
        """PROJECTED_TOOLS and EMPTY_PROJECTION_TOTAL measure what a client was handed."""
        counters = _listing_counters()

        generate_projection(TENANT)  # empty: nothing discovered
        generate_projection(None)  # empty: no identity
        _Fleet({"server_a": ["read_item"]}).send(StartMcpServerCommand(mcp_server_id="server_a"))
        generate_projection(TENANT)  # not empty

        assert _listing_counters() == counters


class TestTheListingServesTheGeneratedProjection:
    def test_tools_list_answers_with_the_projection_generated_for_the_caller(self, monkeypatch) -> None:
        fleet = _Fleet({"server_a": ["read_item", "get_item"], "server_b": ["list_items"]})
        lifecycle.warm_the_front_door_catalogue(fleet)
        get_tool_access_resolver().set_standalone_member_policy(
            "server_a", TENANT, ToolAccessPolicy(deny_list=("get_item",))
        )
        list_tools = _list_handler(monkeypatch)

        result = anyio.run(list_tools, _ctx(TENANT), SimpleNamespace())

        projection = generate_projection(TENANT)
        assert sorted(_names(projection)) == ["list_items", "read_item"]
        assert result.tools == list(projection.tools.values())

    def test_a_listing_that_waits_out_the_warm_up_serves_the_projection_generated_after_it(self, monkeypatch) -> None:
        """The serving half of the diagnosis: the listing arrives mid-warm-up and waits (#1231)."""
        fleet = _Fleet({"server_a": ["read_item"]})
        list_tools = _list_handler(monkeypatch)
        # The boot marks the warm-up in flight before a client can connect.
        catalogue_warmup.warmup_started()
        before = generate_projection(TENANT)
        threading.Timer(0.1, lifecycle.warm_the_front_door_catalogue, args=(fleet,)).start()

        result = anyio.run(list_tools, _ctx(TENANT), SimpleNamespace())

        after = generate_projection(TENANT)
        assert after != before
        assert result.tools == list(after.tools.values())
        assert [t.name for t in result.tools] == ["read_item"]


class TestWhatChangedMeans:
    """Equality is what a later ``tools/list_changed`` will be decided on."""

    def test_regenerating_an_unchanged_catalogue_is_no_change(self) -> None:
        _Fleet({"server_a": ["read_item"]}).send(StartMcpServerCommand(mcp_server_id="server_a"))

        first, second = generate_projection(TENANT), generate_projection(TENANT)

        assert first is not second
        assert first == second

    def test_a_re_discovery_that_reorders_the_registry_is_no_change(self) -> None:
        """A client keys a listing by name, so a new order alone is not a new surface."""
        fleet = _Fleet({"server_a": ["read_item"], "server_b": ["list_items"]})
        lifecycle.warm_the_front_door_catalogue(fleet)
        first = generate_projection(TENANT)

        fleet.rediscover("server_a", [_schema("read_item")])
        second = generate_projection(TENANT)

        assert _names(first) != _names(second), "the re-discovery was meant to reorder the registry"
        assert first == second

    def test_a_changed_definition_under_the_same_name_is_a_change(self) -> None:
        """Same routes, different schema: a client holding the old definition is out of date."""
        fleet = _Fleet({"server_a": ["read_item"]})
        lifecycle.warm_the_front_door_catalogue(fleet)
        first = generate_projection(TENANT)

        fleet.rediscover("server_a", [_schema("read_item", description="Reads one item, now paginated")])
        second = generate_projection(TENANT)

        assert first.routes == second.routes
        assert first != second

    def test_the_same_surface_for_another_identity_is_another_projection(self) -> None:
        _Fleet({"server_a": ["read_item"]}).send(StartMcpServerCommand(mcp_server_id="server_a"))

        mine, theirs = generate_projection(TENANT), generate_projection(OTHER_TENANT)

        assert mine.tools == theirs.tools
        assert mine != theirs
