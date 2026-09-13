"""The projected surface is measured on the served path and read back from ``GET /metrics`` (#1369).

#1059 found five metrics that were defined, incremented and documented with
ready-made PromQL, and never reached a scrape; ``mcp_hangar_projected_tools``,
which this extends, was one of them. So nothing here reads the registry. Every
number is taken from the ``/metrics`` endpoint ``serve --http`` mounts, after
traffic over the real streamable-HTTP transport (`_front_door_harness`), and
the PromQL the guide documents is run against two of those scrapes.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import json
import math
import time
from typing import Any

import pytest

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_tool_projection, projection_metrics, served_tool_names
from mcp_hangar.fastmcp_server.projection_metrics import LastServed
from mcp_hangar.fastmcp_server.served_tool_names import ServedNames
from tests._promql import PROJECTION_QUERIES, Scrape, evaluate, parse_scrape
from tests.integration._front_door_harness import SERVER, TENANT_A, TENANT_B, FrontDoor, front_door

CHANGES = "mcp_hangar_projection_changes_total"
SURFACE = "mcp_hangar_projected_surface_bytes"
UPSTREAM = "mcp_hangar_projected_upstream_bytes"
TOOLS = "mcp_hangar_projected_tools"


@pytest.fixture(autouse=True)
def _fresh_memory(monkeypatch) -> None:
    monkeypatch.setattr(projection_metrics, "LAST_SERVED", LastServed())
    monkeypatch.setattr(served_tool_names, "SERVED", ServedNames())


def _scrape(door: FrontDoor) -> Scrape:
    return parse_scrape(door.scrape(), at=time.time())


def _value(scrape: Scrape, sample: str, **labels: str) -> float:
    return sum(
        value
        for series, value in scrape.samples.get(sample, {}).items()
        if all(dict(series).get(name) == wanted for name, wanted in labels.items())
    )


def _moved(before: Scrape, after: Scrape, sample: str, **labels: str) -> float:
    return _value(after, sample, **labels) - _value(before, sample, **labels)


def _wire_bytes(tools: list[dict[str, Any]]) -> int:
    """The definitions as the client received them, compact UTF-8 JSON."""
    return sum(len(json.dumps(tool, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) for tool in tools)


class TestTheScrapeCarriesThem:
    def test_each_family_is_on_a_real_metrics_response_typed_and_sampled(self) -> None:
        with front_door(("read_item",)) as door:
            door.names(TENANT_A)
            text = door.scrape()

        lines = text.splitlines()
        assert f"# TYPE {CHANGES} counter" in lines
        assert f"# TYPE {SURFACE} histogram" in lines
        assert f"# TYPE {UPSTREAM} histogram" in lines
        samples = parse_scrape(text, at=0.0).samples
        for sample in (CHANGES, f"{SURFACE}_bucket", f"{SURFACE}_count", f"{UPSTREAM}_sum", f"{UPSTREAM}_count"):
            assert samples.get(sample), f"{sample} has a TYPE header and no sample on the scrape"


class TestTheServedPathWritesThem:
    def test_the_bytes_measured_are_the_bytes_the_client_received(self) -> None:
        with front_door(("read_item", "write_item")) as door:
            before = _scrape(door)
            received = door.tools(TENANT_A)
            after = _scrape(door)

        assert sorted(tool["name"] for tool in received) == ["read_item", "write_item"]
        assert _moved(before, after, f"{SURFACE}_sum", kind="governed") == _wire_bytes(received)
        assert _moved(before, after, f"{SURFACE}_count", kind="governed") == 1
        assert _moved(before, after, f"{UPSTREAM}_sum", mcp_server=SERVER) == _wire_bytes(received)
        assert _moved(before, after, f"{UPSTREAM}_count", mcp_server=SERVER) == 1
        assert _moved(before, after, f"{TOOLS}_sum", kind="governed") == 2
        # This caller holds no management tools: an agent principal is not shown them (#904).
        assert _moved(before, after, f"{SURFACE}_count", kind="management") == 1
        assert _moved(before, after, f"{SURFACE}_sum", kind="management") == 0

    def test_a_change_under_a_connected_client_is_counted_once(self) -> None:
        with front_door(("read_item", "write_item"), {TENANT_A: ("write_item",)}) as door:
            assert door.names(TENANT_A) == ["write_item"]  # the first listing: nothing to compare
            first = _scrape(door)
            assert door.names(TENANT_A) == ["write_item"]
            unchanged = _scrape(door)

            # An operator edits the policy under the connected client.
            get_tool_access_resolver().set_standalone_member_policy(
                SERVER, TENANT_A, ToolAccessPolicy(allow_list=("read_item",))
            )
            assert door.names(TENANT_A) == ["read_item"]
            changed = _scrape(door)
            assert door.names(TENANT_A) == ["read_item"]
            settled = _scrape(door)

        assert _moved(first, unchanged, CHANGES) == 0
        assert _moved(unchanged, changed, CHANGES) == 1
        assert _moved(changed, settled, CHANGES) == 0

    def test_a_withdrawal_is_a_change_and_another_tenant_is_not_charged_for_it(self) -> None:
        with front_door(("read_item", "write_item")) as door:
            door.names(TENANT_A)
            door.names(TENANT_B)
            before = _scrape(door)

            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)
            assert door.names(TENANT_A) == ["read_item"]
            assert door.names(TENANT_B) == ["read_item", "write_item"]
            after = _scrape(door)

        assert _moved(before, after, CHANGES) == 1

    def test_the_sdks_listing_before_a_call_measures_nothing_and_moves_no_memory(self, monkeypatch) -> None:
        """#1049: a 2026-07-28 call with arguments makes the SDK list first. The client received nothing."""
        listings: list[str | None] = []
        generate = flat_tool_projection.generate_projection

        def _counting(tenant_id: str | None) -> Any:
            listings.append(tenant_id)
            return generate(tenant_id)

        monkeypatch.setattr(flat_tool_projection, "generate_projection", _counting)

        with front_door(("read_item", "write_item")) as door:
            door.names(TENANT_A)
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)
            before = _scrape(door)
            del listings[:]

            assert door.result(TENANT_A, "read_item", {"x": "1"})["content"] == [
                {"type": "text", "text": "did read_item"}
            ]
            after_call = _scrape(door)
            assert listings == [TENANT_A], "the SDK did not list before the call, so this test would prove nothing"

            assert door.names(TENANT_A) == ["read_item"]
            after_listing = _scrape(door)

        for sample, labels in (
            (CHANGES, {}),
            (f"{SURFACE}_count", {"kind": "governed"}),
            (f"{UPSTREAM}_count", {"mcp_server": SERVER}),
            (f"{TOOLS}_count", {"kind": "governed"}),
        ):
            assert _moved(before, after_call, sample, **labels) == 0, f"the pre-dispatch listing moved {sample}"
        # The pre-dispatch listing saw the withdrawal and did not remember it, so the
        # client's own listing is the one that counts the change.
        assert _moved(after_call, after_listing, CHANGES) == 1


class TestNoTenantLabel:
    def test_no_series_of_the_surface_families_names_a_tenant(self) -> None:
        with front_door(("read_item", "write_item"), {TENANT_B: ("read_item",)}) as door:
            door.names(TENANT_A)
            door.names(TENANT_B)
            get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)
            door.names(TENANT_A)
            text = door.scrape()

        families = (CHANGES, SURFACE, UPSTREAM, TOOLS)
        for sample, series in parse_scrape(text, at=0.0).samples.items():
            if not sample.startswith(families):
                continue
            for labels in series:
                assert {name for name, _ in labels} <= {"kind", "mcp_server", "le"}, (sample, labels)
        for line in text.splitlines():
            if line.startswith(families):
                assert TENANT_A not in line and TENANT_B not in line, line


class TestTheDocumentedPromQLRuns:
    """Every query in `PROJECTION_QUERIES` is the one the guide prints, run against the gateway's scrape."""

    def test_each_documented_query_answers_from_two_scrapes_of_the_gateway(self) -> None:
        with front_door(("read_item", "write_item"), {TENANT_A: ("write_item",)}) as door:
            # Series exist before the first scrape, as they would for a Prometheus
            # that has been scraping all along: a rate needs two samples.
            door.names(TENANT_A)
            door.names(TENANT_B)
            before = _scrape(door)

            listed = [door.tools(TENANT_A)]
            get_tool_access_resolver().set_standalone_member_policy(
                SERVER, TENANT_A, ToolAccessPolicy(allow_list=("read_item", "write_item"))
            )
            listed += [door.tools(TENANT_A), door.tools(TENANT_B)]
            after = _scrape(door)
            listed_again = door.tools(TENANT_A)
            quiet = _scrape(door)

        sizes = [_wire_bytes(tools) for tools in listed]
        mean_bytes = sum(sizes) / len(sizes)
        mean_tools = sum(len(tools) for tools in listed) / len(listed)
        results = {name: evaluate(query, before, after) for name, query in PROJECTION_QUERIES.items()}
        governed, management = frozenset({("kind", "governed")}), frozenset({("kind", "management")})

        assert results["changed"] == {frozenset(): 1.0}, "the fleet changed under tenant A and the query says no"
        assert evaluate(PROJECTION_QUERIES["changed"], after, quiet) == {}, "nothing changed and the query says yes"
        assert listed_again == listed[1]

        assert results["tools_per_listing"] == pytest.approx({governed: mean_tools, management: 0.0})
        assert results["bytes_per_listing"] == pytest.approx({governed: mean_bytes, management: 0.0})
        per_upstream = results["bytes_per_upstream"]
        assert isinstance(per_upstream, dict)
        assert per_upstream[frozenset({("mcp_server", SERVER)})] == pytest.approx(mean_bytes)
        # An upstream no listing included in the window reads 0/0, as it does in
        # Prometheus. The process may hold such series from earlier traffic.
        assert all(math.isnan(value) for labels, value in per_upstream.items() if ("mcp_server", SERVER) not in labels)

        p95 = results["governed_bytes_p95"]
        assert isinstance(p95, dict) and set(p95) == {frozenset()}
        assert 0 < p95[frozenset()] <= 1024, "every listing was under 1 KiB, so its bucket is (0, 1024]"
        assert max(sizes) <= 1024
