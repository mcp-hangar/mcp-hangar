"""`/metrics` says which L7 egress policies a replica holds, and when each last arrived (#1562).

In #1306 a gateway served calls ungoverned for 2h16m after a restart while the
MCPEgressPolicy CR read Enforce throughout, and nothing on the platform said so.
`mcp_hangar_l7_policy_held` is read from the servers at scrape time, so it
follows every path that installs a policy; the timestamp is written by
`McpServer` itself. Each path gets its own test here, because a path the metric
missed is exactly the regression it exists to catch. The end-to-end restart
checks are in ``tests/integration/test_a_restart_keeps_an_l7_policy_set_over_the_api.py``.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest

from mcp_hangar import metrics
from mcp_hangar.application.commands.crud_commands import SetL7PolicyCommand
from mcp_hangar.application.commands.crud_handlers import SetL7PolicyHandler
from mcp_hangar.application.event_handlers.fleet_projection import FleetProjection
from mcp_hangar.domain.contracts.metrics_publisher import (
    get_default_metrics_publisher,
    set_default_metrics_publisher,
)
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.events.enforcement import EgressPolicySet
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.services.fleet_snapshot import server_from_snapshot
from mcp_hangar.infrastructure.metrics_publisher import PrometheusMetricsPublisher
from mcp_hangar.infrastructure.persistence.config_repository import InMemoryMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService

ENFORCE = {"defaultAction": "Allow", "mode": "Enforce", "tools": {"deny": ["add"]}}
AUDIT = {"defaultAction": "Allow", "mode": "Audit", "tools": {"deny": ["add"]}}
HELD = re.compile(r'^mcp_hangar_l7_policy_held\{mcp_server="([^"]+)",mode="([^"]+)"\} (\S+)$', re.M)
LAST_SET = re.compile(r'^mcp_hangar_l7_policy_last_set_timestamp_seconds\{mcp_server="([^"]+)"\} (\S+)$', re.M)


def _held() -> dict[str, tuple[str, float]]:
    return {server: (mode, float(value)) for server, mode, value in HELD.findall(metrics.get_metrics())}


def _last_set() -> dict[str, float]:
    return {server: float(value) for server, value in LAST_SET.findall(metrics.get_metrics())}


def _server(mcp_server_id: str = "math") -> McpServer:
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"])


@pytest.fixture
def fleet() -> Iterator[InMemoryMcpServerRepository]:
    """A replica's fleet, wired to the scrape the way bootstrap wires it."""
    previous = get_default_metrics_publisher()
    set_default_metrics_publisher(PrometheusMetricsPublisher())
    metrics.L7_POLICY_LAST_SET_SECONDS._values.clear()
    repository = InMemoryMcpServerRepository()
    metrics.read_l7_policies_from(repository)
    yield repository
    metrics.read_l7_policies_from(None)
    metrics.L7_POLICY_LAST_SET_SECONDS._values.clear()
    set_default_metrics_publisher(previous)


class TestAnOperatorPush:
    def test_a_push_shows_as_held_with_a_timestamp_in_the_push_window(self, fleet) -> None:
        fleet.add("math", _server())
        handler = SetL7PolicyHandler(fleet, event_bus=Mock())

        before = time.time()
        handler.handle(SetL7PolicyCommand(mcp_server_id="math", policy=L7Policy.from_dict(ENFORCE), source="operator"))
        after = time.time()

        assert _held() == {"math": ("Enforce", 1.0)}
        assert before <= _last_set()["math"] <= after

    def test_an_audit_policy_is_labelled_audit(self, fleet) -> None:
        fleet.add("math", _server())
        fleet.get("math").set_l7_policy(L7Policy.from_dict(AUDIT))

        assert _held() == {"math": ("Audit", 1.0)}

    def test_a_clear_drops_the_held_series_and_moves_the_timestamp(self, fleet) -> None:
        fleet.add("math", _server())
        handler = SetL7PolicyHandler(fleet, event_bus=Mock())
        handler.handle(SetL7PolicyCommand(mcp_server_id="math", policy=L7Policy.from_dict(ENFORCE), source="operator"))
        pushed = _last_set()["math"]
        time.sleep(0.01)

        handler.handle(SetL7PolicyCommand(mcp_server_id="math", policy=None, source="operator"))

        assert "math" not in _held()
        assert _last_set()["math"] > pushed

    def test_a_server_without_a_policy_is_absent_not_zero(self, fleet) -> None:
        fleet.add("math", _server())
        fleet.add("other", _server("other"))
        fleet.get("other").set_l7_policy(L7Policy.from_dict(ENFORCE))

        assert _held() == {"other": ("Enforce", 1.0)}
        assert "math" not in _last_set()


class TestEveryOtherInstallPath:
    @pytest.mark.asyncio
    async def test_a_policy_restored_by_startup_recovery_is_held_and_stamped(self, fleet) -> None:
        # A restart on a durable backend: the file builds the server, recovery
        # puts the operator's stored policy back on it (#1560).
        configs = InMemoryMcpServerConfigRepository()
        await configs.save(
            McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess", command=["python"], l7_policy=ENFORCE)
        )
        fleet.add("math", _server())
        assert "math" not in _held()

        before = time.time()
        await RecoveryService(
            database=AsyncMock(), mcp_server_repository=fleet, config_repository=configs, audit_repository=AsyncMock()
        ).recover_mcp_servers()

        assert _held() == {"math": ("Enforce", 1.0)}
        assert _last_set()["math"] >= before

    def test_a_policy_a_peer_set_is_held_and_stamped(self, fleet) -> None:
        # Another replica took the push; this one learns of it from the event
        # tail and reads the policy from the shared row.
        configs = InMemoryMcpServerConfigRepository()
        asyncio.run(
            configs.save(
                McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess", command=["python"], l7_policy=AUDIT)
            )
        )
        fleet.add("math", _server())

        class _SyncLoop:
            def run(self, coro, timeout):
                return asyncio.run(coro)

        before = time.time()
        FleetProjection(fleet, configs, _SyncLoop()).handle(
            EgressPolicySet(mcp_server_id="math", source="operator", mode="Audit", default_action="Allow")
        )

        assert _held() == {"math": ("Audit", 1.0)}
        assert _last_set()["math"] >= before

    def test_a_server_rebuilt_from_its_row_with_a_policy_is_held_and_stamped(self, fleet) -> None:
        # How recovery and the peer tail bring back a server registered over
        # REST: construction with the policy, no setter call.
        before = time.time()
        fleet.add(
            "math",
            server_from_snapshot(
                McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess", command=["python"], l7_policy=ENFORCE)
            ),
        )

        assert _held() == {"math": ("Enforce", 1.0)}
        assert _last_set()["math"] >= before


class TestARemovedServer:
    def test_takes_its_held_and_timestamp_series_with_it(self, fleet) -> None:
        fleet.add("math", _server())
        fleet.get("math").set_l7_policy(L7Policy.from_dict(ENFORCE))
        assert "math" in _held() and "math" in _last_set()

        fleet.remove("math")
        metrics.remove_mcp_server_series("math")

        assert "math" not in _held()
        assert "math" not in _last_set()


def test_a_source_that_fails_does_not_fail_the_scrape(fleet) -> None:
    broken = Mock()
    broken.get_all.side_effect = RuntimeError("boom")
    metrics.read_l7_policies_from(broken)

    scrape = metrics.get_metrics()

    assert "# TYPE mcp_hangar_l7_policy_held gauge" in scrape
    assert _held() == {}
