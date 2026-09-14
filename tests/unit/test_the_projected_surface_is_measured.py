"""What a served listing weighs, what it is made of, and whether it changed (#1369).

These pin the pieces: the digest keeps `Projection` equality, the memory is
bounded, a change is counted only against what the same identity was served
before, and nothing carries a tenant. The served path itself, over the real
transport and scraped from ``/metrics``, is
``tests/integration/test_the_projected_surface_is_measured.py``.

Naming: neutral placeholders only (server_a, group_a, read_item, tenant:a).
"""

from __future__ import annotations

from collections import defaultdict
from types import MappingProxyType, SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar._sdk_compat import Tool as MCPTool
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection, projection_metrics
from mcp_hangar.fastmcp_server.flat_tool_projection import Projection
from mcp_hangar.fastmcp_server.projection_metrics import (
    LastServed,
    definition_bytes,
    observe_served_listing,
    projection_digest,
)

KEY_A = ("tenant:a", "user", "user:one", None, None)
KEY_B = ("tenant:b", "user", "user:two", None, None)


def _tool(name: str, description: str = "") -> MCPTool:
    return MCPTool.model_validate(
        {
            "name": name,
            "description": description or f"Does {name}",
            "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}},
        }
    )


def _projection(*entries: tuple[str, str, str], tenant_id: str = "tenant:a", description: str = "") -> Projection:
    """A projection of ``(flat name, server, upstream tool)`` entries, in the order given."""
    return Projection(
        tenant_id=tenant_id,
        routes=MappingProxyType({name: (server, tool) for name, server, tool in entries}),
        tools=MappingProxyType({name: _tool(name, description) for name, _, _ in entries}),
    )


def _digest(projection: Projection) -> bytes:
    return projection_digest(projection, {name: definition_bytes(tool) for name, tool in projection.tools.items()})


def _changes() -> float:
    return sum(sample.value for sample in prometheus_metrics.PROJECTION_CHANGES_TOTAL.collect())


def _histogram(metric: Any, stat: str, **labels: str) -> float:
    _, sums, counts = metric.collect()
    samples = sums if stat == "sum" else counts
    return sum(sample.value for sample in samples if all(sample.labels.get(k) == v for k, v in labels.items()))


@pytest.fixture(autouse=True)
def _fresh_memory(monkeypatch) -> None:
    monkeypatch.setattr(projection_metrics, "LAST_SERVED", LastServed())


def _as(monkeypatch, key: tuple | None) -> None:
    monkeypatch.setattr(projection_metrics, "served_key", lambda: key)


class TestTheDigestKeepsProjectionEquality:
    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param(
                _projection(("read_item", "server_a", "read_item"), ("list_items", "server_a", "list_items")),
                _projection(("list_items", "server_a", "list_items"), ("read_item", "server_a", "read_item")),
                id="reordered",
            ),
            pytest.param(
                _projection(("read_item", "server_a", "read_item")),
                _projection(("read_item", "server_a", "read_item")),
                id="regenerated",
            ),
        ],
    )
    def test_equal_projections_share_a_digest(self, first: Projection, second: Projection) -> None:
        assert first == second
        assert _digest(first) == _digest(second)

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            pytest.param(
                _projection(("read_item", "server_a", "read_item")),
                _projection(("read_item", "server_a", "read_item"), description="Reads one item, now paginated"),
                id="a changed definition under the same name",
            ),
            pytest.param(
                _projection(("read_item", "server_a", "read_item")),
                _projection(("read_item", "server_b", "read_item")),
                id="the same name routed to another upstream",
            ),
            pytest.param(
                _projection(("read_item", "server_a", "read_item")),
                _projection(("read_item", "server_a", "read_item"), ("list_items", "server_a", "list_items")),
                id="a name added",
            ),
            pytest.param(
                _projection(),
                _projection(("read_item", "server_a", "read_item")),
                id="from nothing to something",
            ),
        ],
    )
    def test_different_projections_do_not(self, first: Projection, second: Projection) -> None:
        assert first != second
        assert _digest(first) != _digest(second)

    def test_a_definition_is_measured_as_the_wire_carries_it(self) -> None:
        """Compact, UTF-8, no ``null`` fields: what the modern transport sends."""
        encoded = definition_bytes(_tool("read_item", "Liest ein Element"))

        assert b"null" not in encoded and b": " not in encoded and b", " not in encoded
        assert encoded.startswith(b"{") and encoded.endswith(b"}")
        assert b"Liest ein Element" in encoded


class TestTheMemoryIsBounded:
    def test_it_hands_back_what_it_replaced(self) -> None:
        memory = LastServed()

        assert memory.exchange(KEY_A, b"one") is None
        assert memory.exchange(KEY_A, b"two") == b"one"
        assert len(memory) == 1

    def test_the_least_recently_served_identity_is_forgotten_first(self) -> None:
        memory = LastServed(max_identities=2)
        memory.exchange(KEY_A, b"a")
        memory.exchange(KEY_B, b"b")
        memory.exchange(KEY_A, b"a")  # A is served again: B is now the oldest

        memory.exchange(("tenant:c", "user", None, None, None), b"c")

        assert len(memory) == 2
        assert memory.exchange(KEY_B, b"b") is None, "the oldest identity was kept"
        assert memory.exchange(("tenant:c", "user", None, None, None), b"c") == b"c"

    def test_it_holds_at_least_one_identity(self) -> None:
        with pytest.raises(ValueError):
            LastServed(max_identities=0)


class TestAChangeIsCountedAgainstTheSameIdentity:
    def test_a_first_listing_is_not_a_change_and_an_unchanged_one_is_not_either(self, monkeypatch) -> None:
        _as(monkeypatch, KEY_A)
        before = _changes()

        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})

        assert _changes() == before

    def test_a_listing_that_differs_from_the_last_one_is_one_change(self, monkeypatch) -> None:
        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})
        before = _changes()

        observe_served_listing(_projection(("list_items", "server_a", "list_items")), [], {})
        observe_served_listing(_projection(("list_items", "server_a", "list_items")), [], {})

        assert _changes() == before + 1

    def test_from_an_empty_listing_to_a_full_one_is_a_change(self, monkeypatch) -> None:
        """The diagnosed case (#1365): served nothing mid-warm-up, then the catalogue landed."""
        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(), [], {})
        before = _changes()

        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})

        assert _changes() == before + 1

    def test_another_identity_is_compared_only_with_itself(self, monkeypatch) -> None:
        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})
        before = _changes()

        _as(monkeypatch, KEY_B)
        observe_served_listing(_projection(("list_items", "server_a", "list_items"), tenant_id="tenant:b"), [], {})

        assert _changes() == before

    def test_a_caller_without_a_tenant_is_never_remembered(self, monkeypatch) -> None:
        _as(monkeypatch, None)
        before = _changes()

        observe_served_listing(_projection(), [], {})
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})

        assert _changes() == before
        assert len(projection_metrics.LAST_SERVED) == 0

    def test_an_evicted_identity_starts_over_so_a_change_can_be_missed_but_never_invented(self, monkeypatch) -> None:
        monkeypatch.setattr(projection_metrics, "LAST_SERVED", LastServed(max_identities=1))
        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [], {})
        _as(monkeypatch, KEY_B)
        observe_served_listing(_projection(tenant_id="tenant:b"), [], {})
        before = _changes()

        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(("list_items", "server_a", "list_items")), [], {})

        assert _changes() == before


class TestSizeAndComposition:
    def test_bytes_are_split_by_kind_and_by_upstream_with_a_group_read_as_its_group(self, monkeypatch) -> None:
        _as(monkeypatch, None)
        projection = _projection(
            ("read_item", "member_1", "read_item"),
            ("list_items", "member_2", "list_items"),
            ("get_item", "server_b", "get_item"),
        )
        management = [_tool("hangar_list")]
        sizes = {name: len(definition_bytes(tool)) for name, tool in projection.tools.items()}
        governed_before = _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "sum", kind="governed")
        managed_before = _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "sum", kind="management")
        group_before = _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "sum", mcp_server="group_a")
        group_count_before = _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "count", mcp_server="group_a")
        tools_before = _histogram(prometheus_metrics.PROJECTED_TOOLS, "sum", kind="governed")

        observe_served_listing(projection, management, {"member_1": "group_a", "member_2": "group_a"})

        assert _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "sum", kind="governed") == governed_before + sum(
            sizes.values()
        )
        assert _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "sum", kind="management") == managed_before + len(
            definition_bytes(management[0])
        )
        assert _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "sum", mcp_server="group_a") == group_before + (
            sizes["read_item"] + sizes["list_items"]
        )
        assert _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "count", mcp_server="group_a") == (
            group_count_before + 1
        ), "one listing is one observation per upstream, not one per tool"
        assert _histogram(prometheus_metrics.PROJECTED_TOOLS, "sum", kind="governed") == tools_before + 3

    def test_no_series_carries_a_tenant(self, monkeypatch) -> None:
        _as(monkeypatch, KEY_A)
        observe_served_listing(_projection(("read_item", "server_a", "read_item")), [_tool("hangar_list")], {})
        observe_served_listing(_projection(("get_item", "server_a", "get_item")), [], {})

        buckets, sums, counts = prometheus_metrics.PROJECTED_SURFACE_BYTES.collect()
        upstream = prometheus_metrics.PROJECTED_UPSTREAM_BYTES.collect()
        samples = [
            *buckets,
            *sums,
            *counts,
            *upstream[0],
            *upstream[1],
            *upstream[2],
            *prometheus_metrics.PROJECTION_CHANGES_TOTAL.collect(),
        ]
        assert samples
        for sample in samples:
            assert set(sample.labels) <= {"kind", "mcp_server", "le"}, sample.labels
            assert not any("tenant" in value for value in sample.labels.values()), sample.labels


def _principal() -> Principal:
    return Principal(id=PrincipalId("user:one"), type=PrincipalType.USER, tenant_id="tenant:a")


def _ctx(*, envelope: str) -> SimpleNamespace:
    body = (
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"read_item","arguments":{"x":"1"}}}'
        if envelope == "tools/call"
        else b'{"jsonrpc":"2.0","method":"tools/list","params":{}}'
    )
    request = SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=_principal())), _body=body)
    return SimpleNamespace(request=request)


def _handlers(monkeypatch, served: list[Projection]) -> dict[str, Any]:
    """The registered handlers, serving whichever projection ``served[0]`` holds."""
    monkeypatch.setattr(flat_tool_projection, "generate_projection", lambda _tenant: served[0])
    monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: set())
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


class TestOnlyAListingTheClientReceivedIsMeasured:
    def test_the_sdks_listing_before_a_call_measures_nothing_and_moves_no_memory(self, monkeypatch) -> None:
        """#1049: the pre-dispatch listing is not a listing the client received."""
        served = [_projection(("read_item", "server_a", "read_item"))]
        list_tools = _handlers(monkeypatch, served)["tools/list"]
        anyio.run(list_tools, _ctx(envelope="tools/list"), SimpleNamespace())
        served[0] = _projection(("list_items", "server_a", "list_items"))
        before = (
            _changes(),
            _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "count", kind="governed"),
            _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "count", mcp_server="server_a"),
        )

        anyio.run(list_tools, _ctx(envelope="tools/call"), SimpleNamespace())

        assert (
            _changes(),
            _histogram(prometheus_metrics.PROJECTED_SURFACE_BYTES, "count", kind="governed"),
            _histogram(prometheus_metrics.PROJECTED_UPSTREAM_BYTES, "count", mcp_server="server_a"),
        ) == before
        # The client's own listing still compares with the one it last received.
        anyio.run(list_tools, _ctx(envelope="tools/list"), SimpleNamespace())
        assert _changes() == before[0] + 1


class TestTheCounterIsOnTheExpositionFromBoot:
    @pytest.fixture(autouse=True)
    def _front_door_topology(self):
        reset_tool_access_resolver()
        get_tool_access_resolver().set_topology_mode("front_door")
        yield
        reset_tool_access_resolver()

    def test_installing_the_front_door_puts_the_counter_at_zero(self, monkeypatch) -> None:
        """Without the seed the first change reads as no increase: the series would appear at 1."""

        def _sample_lines() -> list[str]:
            return [
                line
                for line in prometheus_metrics.get_metrics().splitlines()
                if line.startswith("mcp_hangar_projection_changes_total")
            ]

        monkeypatch.setattr(prometheus_metrics.PROJECTION_CHANGES_TOTAL, "_values", defaultdict(float))
        assert _sample_lines() == [], "a fresh process was meant to have no sample yet"

        class _Low:
            def add_request_handler(self, method, params_type, handler):
                pass

        assert flat_tool_projection.maybe_register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low())) is True

        assert _sample_lines() == ["mcp_hangar_projection_changes_total 0.0"]
