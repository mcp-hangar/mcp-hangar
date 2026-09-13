"""A member's health events reach its group, and a recovered group closes its circuit (#1355).

The served path is proved in
``tests/integration/test_a_passing_health_check_returns_a_member_to_rotation.py``.
These tests pin its parts on their own:

- The saga reads a member's groups from the live mapping it was given, so it
  finds a group filled in after the saga was built, with nothing registered.
- A stop is not a failure, whichever of its two reasons it carries.
- A failure is counted once. One failing health check that also degrades the
  server counts once, and a failed start that degrades it counts once too.
- ``report_success`` closes an open circuit, but only once ``min_healthy``
  members are in rotation.
"""

from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.sagas import GroupRebalanceSaga
from mcp_hangar.domain.events import (
    DEGRADED_BY_HEALTH_CHECKS,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerStopped,
)
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import GroupCircuitClosed, McpServerGroup
from mcp_hangar.domain.value_objects import GroupState, McpServerState, ProviderState


def _server(server_id: str) -> MagicMock:
    server = MagicMock()
    server.id = server_id
    server.mcp_server_id = server_id
    server.state = ProviderState.READY
    server.state_snapshot = ProviderState.READY
    return server


def _tripped(group_id: str, member_ids: list[str], min_healthy: int = 1) -> McpServerGroup:
    """A group whose every member was driven out, the last failure opening its circuit."""
    group = McpServerGroup(
        group_id=group_id,
        auto_start=False,
        min_healthy=min_healthy,
        unhealthy_threshold=1,
        healthy_threshold=1,
        circuit_failure_threshold=len(member_ids),
    )
    for member_id in member_ids:
        group.add_member(_server(member_id))
        group.get_member(member_id).in_rotation = True
    for member_id in member_ids:
        group.report_failure(member_id)
    assert group.circuit_open is True and group.healthy_count == 0
    group.collect_events()
    return group


def _passed(server_id: str) -> HealthCheckPassed:
    return HealthCheckPassed(mcp_server_id=server_id, duration_ms=1.0)


class TestTheSagaReadsMembershipFromTheGroups:
    def test_a_group_filled_in_after_the_saga_was_built_is_found(self):
        """What `bootstrap()` does: saga first, with the live dict; groups after."""
        groups: dict[str, McpServerGroup] = {}
        saga = GroupRebalanceSaga(groups=groups)
        groups["pool"] = _tripped("pool", ["a"])

        saga.handle(_passed("a"))

        member = groups["pool"].get_member("a")
        assert member.in_rotation is True
        assert member.consecutive_failures == 0

    def test_a_member_of_two_groups_is_reported_to_both(self):
        groups = {"east": _tripped("east", ["a"]), "west": _tripped("west", ["a"])}
        saga = GroupRebalanceSaga(groups=groups)

        saga.handle(_passed("a"))

        assert groups["east"].get_member("a").in_rotation is True
        assert groups["west"].get_member("a").in_rotation is True

    def test_a_failed_check_reaches_the_group(self):
        group = McpServerGroup(group_id="pool", auto_start=False, unhealthy_threshold=2)
        group.add_member(_server("a"))
        saga = GroupRebalanceSaga(groups={"pool": group})

        saga.handle(HealthCheckFailed(mcp_server_id="a", consecutive_failures=1, error_message="down"))

        assert group.get_member("a").consecutive_failures == 1

    def test_a_server_in_no_group_changes_nothing(self):
        group = _tripped("pool", ["a"])
        saga = GroupRebalanceSaga(groups={"pool": group})

        assert saga.handle(_passed("elsewhere")) == []
        assert group.get_member("a").in_rotation is False


def _strict(member_ids: list[str], circuit_failure_threshold: int = 1) -> McpServerGroup:
    """Members in rotation, and a group that reacts to the first counted failure."""
    group = McpServerGroup(
        group_id="pool",
        auto_start=False,
        unhealthy_threshold=1,
        circuit_failure_threshold=circuit_failure_threshold,
    )
    for member_id in member_ids:
        group.add_member(_server(member_id))
        group.get_member(member_id).in_rotation = True
    return group


class TestAStopIsNotAFailure:
    """The only two reasons `McpServerStopped` carries; the gateway or an operator chose both."""

    @pytest.mark.parametrize("reason", ["idle", "shutdown"])
    def test_a_stopped_member_stays_in_rotation_and_the_circuit_stays_closed(self, reason):
        group = _strict(["a"])
        saga = GroupRebalanceSaga(groups={"pool": group})

        for _ in range(3):
            saga.handle(McpServerStopped(mcp_server_id="a", reason=reason))

        member = group.get_member("a")
        assert member.in_rotation is True
        assert member.consecutive_failures == 0
        assert group.circuit_open is False


class TestAFailureIsCountedOnce:
    def test_one_failing_health_check_counts_once_even_when_it_degrades_the_server(self):
        """At the degrade threshold one check emits HealthCheckFailed AND McpServerDegraded.

        Driven through the real `McpServer.health_check()`, so the pairing is
        the aggregate's, not an assumption of this test.
        """
        server = McpServer(mcp_server_id="a", mode="subprocess", command=["echo"])
        server._client = MagicMock(call=Mock(side_effect=OSError("down")))
        server._state = McpServerState.READY
        # Four counted failures would open this circuit; three checks must not.
        group = McpServerGroup(group_id="pool", auto_start=False, unhealthy_threshold=10, circuit_failure_threshold=4)
        group.add_member(server)
        saga = GroupRebalanceSaga(groups={"pool": group})

        per_check = []
        for _ in range(3):
            server.health_check()
            events = server.collect_events()
            per_check.append(events)
            for event in events:
                saga.handle(event)

        last = per_check[-1]
        assert [type(e) for e in last] == [HealthCheckFailed, McpServerDegraded]
        assert last[1].reason == DEGRADED_BY_HEALTH_CHECKS
        assert group.get_member("a").consecutive_failures == 3
        assert group.circuit_open is False

    def test_a_failed_start_that_degrades_the_server_counts_once(self):
        """No health check reported it, so the degrade is the group's only signal."""
        group = _strict(["a"], circuit_failure_threshold=2)
        saga = GroupRebalanceSaga(groups={"pool": group})

        saga.handle(
            McpServerDegraded(mcp_server_id="a", consecutive_failures=3, total_failures=3, reason="connection refused")
        )

        assert group.get_member("a").consecutive_failures == 1
        assert group.get_member("a").in_rotation is False
        assert group.circuit_open is False


class TestARecoveredGroupClosesItsCircuit:
    def test_with_min_healthy_one_one_member_back_closes_the_circuit(self):
        group = _tripped("pool", ["a", "b"], min_healthy=1)

        group.report_success("a")

        assert group.get_member("b").in_rotation is False
        assert group.circuit_open is False
        assert group.state == GroupState.HEALTHY
        assert [type(e) for e in group.collect_events()].count(GroupCircuitClosed) == 1

    def test_below_min_healthy_the_circuit_stays_open(self):
        group = _tripped("pool", ["a", "b"], min_healthy=2)

        group.report_success("a")

        assert group.get_member("a").in_rotation is True
        assert group.circuit_open is True
        assert group.state == GroupState.DEGRADED

        group.report_success("b")

        assert group.circuit_open is False
        assert group.state == GroupState.HEALTHY

    def test_a_success_with_the_circuit_closed_emits_no_close(self):
        group = McpServerGroup(group_id="pool", auto_start=False)
        group.add_member(_server("a"))
        group.get_member("a").in_rotation = True
        group.collect_events()

        group.report_success("a")

        assert group.circuit_open is False
        assert GroupCircuitClosed not in [type(e) for e in group.collect_events()]
