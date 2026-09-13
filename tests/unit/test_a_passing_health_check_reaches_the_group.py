"""A member's health events reach its group, and a recovered group closes its circuit (#1355).

The served path is proved in
``tests/integration/test_a_passing_health_check_returns_a_member_to_rotation.py``.
These tests pin its two halves on their own. The saga reads a member's groups
from the live mapping it was given, so it finds a group filled in after the saga
was built, with nothing registered. ``report_success`` closes an open circuit,
but only once ``min_healthy`` members are in rotation.
"""

from unittest.mock import MagicMock

from mcp_hangar.application.sagas import GroupRebalanceSaga
from mcp_hangar.domain.events import HealthCheckFailed, HealthCheckPassed
from mcp_hangar.domain.model.mcp_server_group import GroupCircuitClosed, McpServerGroup
from mcp_hangar.domain.value_objects import GroupState, ProviderState


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
