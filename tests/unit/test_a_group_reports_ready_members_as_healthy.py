"""A group's `healthy_count` counts ready members, and nothing the group decides reads it (#1356).

`healthy_count` counted every member in rotation that was not dead, `cold` ones
included. A group whose members the GC had reaped reported them all healthy,
and with its circuit open it reported `healthy_count: 3, is_available: false,
circuit_open: true`. It now counts members that are `ready` and in rotation,
and `members_in_rotation_count` reports rotation size.

What the group decides still counts the members in rotation that are not dead:
`is_available`, its state, and whether a success closes an open circuit. A
group of `cold` members has to keep routing, because the next call through it
is what starts one. The served path is proved in
``tests/integration/test_a_cold_group_reports_no_healthy_members_and_still_routes.py``.
"""

from unittest.mock import MagicMock

from mcp_hangar.domain.model.mcp_server import DEAD_CRASHED
from mcp_hangar.domain.model.mcp_server_group import GroupStateChanged, McpServerGroup
from mcp_hangar.domain.value_objects import GroupState, McpServerState

COLD, READY, DEAD = McpServerState.COLD, McpServerState.READY, McpServerState.DEAD


def _server(server_id: str) -> MagicMock:
    server = MagicMock()
    server.id = server_id
    server.mcp_server_id = server_id
    server.state = READY
    server.state_snapshot = READY
    server.dead_reason_snapshot = None
    return server


def _group(member_ids: list[str], min_healthy: int = 1, circuit_threshold: int = 10) -> McpServerGroup:
    """Members started and in rotation, as a group with auto-start leaves them.

    They stay in rotation however often they fail: only the counts and the
    circuit are under test.
    """
    group = McpServerGroup(
        group_id="pool",
        auto_start=False,
        min_healthy=min_healthy,
        unhealthy_threshold=100,
        healthy_threshold=1,
        circuit_failure_threshold=circuit_threshold,
    )
    for member_id in member_ids:
        group.add_member(_server(member_id))
        group.get_member(member_id).in_rotation = True
        group.report_success(member_id)
    group.collect_events()
    return group


def _set(group: McpServerGroup, member_id: str, state: McpServerState) -> None:
    """The member's server moves to `state` without telling the group, as an idle reap or a crash does."""
    member = group.get_member(member_id)
    assert member is not None
    member.mcp_server.state_snapshot = state
    member.mcp_server.dead_reason_snapshot = DEAD_CRASHED if state is DEAD else None


def _open_circuit(group: McpServerGroup, member_id: str, failures: int) -> None:
    for _ in range(failures):
        group.report_failure(member_id)
    assert group.circuit_open is True


class TestAGroupWhoseMembersAreCold:
    def test_reports_no_healthy_members_and_every_member_in_rotation(self):
        group = _group(["a", "b", "c"])
        for member_id in ("a", "b", "c"):
            _set(group, member_id, COLD)

        status = group.to_status_dict()

        assert (status["healthy_count"], status["members_in_rotation_count"], status["total_members"]) == (0, 3, 3)
        assert [m["in_rotation"] for m in status["members"]] == [True, True, True]

    def test_is_still_available_and_selects_a_member_to_start(self):
        group = _group(["a", "b"])
        _set(group, "a", COLD)
        _set(group, "b", COLD)

        assert group.is_available is True
        assert group.state is GroupState.HEALTHY
        assert group.select_member() is not None

    def test_with_its_circuit_open_it_no_longer_reports_them_healthy(self):
        # The shape in the issue: three members in rotation, the circuit open.
        group = _group(["a", "b", "c"], circuit_threshold=2)
        for member_id in ("a", "b", "c"):
            _set(group, member_id, COLD)
        _open_circuit(group, "a", failures=2)

        status = group.to_status_dict()

        assert (status["circuit_open"], status["is_available"]) == (True, False)
        assert (status["healthy_count"], status["members_in_rotation_count"]) == (0, 3)


class TestWhatTheGroupDecides:
    def test_a_cold_member_in_rotation_still_counts_toward_min_healthy(self):
        # One member ready and one cold is two toward min_healthy, as it was
        # before #1356, though only one is healthy.
        group = _group(["a", "b"], min_healthy=2, circuit_threshold=2)
        _set(group, "b", COLD)
        _open_circuit(group, "a", failures=2)

        group.report_success("a")

        assert group.circuit_open is False
        assert group.state is GroupState.HEALTHY
        assert (group.healthy_count, group.members_in_rotation_count) == (1, 2)

    def test_a_crashed_member_in_rotation_does_not_count_toward_min_healthy(self):
        group = _group(["a", "b"], min_healthy=2, circuit_threshold=2)
        _set(group, "b", DEAD)
        _open_circuit(group, "a", failures=2)

        group.report_success("a")

        assert group.circuit_open is True
        assert group.state is GroupState.DEGRADED
        assert (group.healthy_count, group.members_in_rotation_count) == (1, 2)

    def test_a_group_whose_only_member_in_rotation_crashed_is_not_available(self):
        group = _group(["a"])
        _set(group, "a", DEAD)
        group.report_member_dead("a")

        assert group.is_available is False
        assert group.state is GroupState.INACTIVE
        assert (group.healthy_count, group.members_in_rotation_count) == (0, 1)


def test_a_state_change_carries_the_counts_the_status_surfaces_report():
    group = _group(["a", "b"], circuit_threshold=2)
    _set(group, "b", COLD)

    _open_circuit(group, "a", failures=2)

    [changed] = [e for e in group.collect_events() if isinstance(e, GroupStateChanged)]
    assert (changed.old_state, changed.new_state) == ("healthy", "degraded")
    assert (changed.healthy_count, changed.members_in_rotation_count, changed.total_count) == (1, 2, 2)
