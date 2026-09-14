"""A group's circuit counts failures in a row, not over the life of the process (#1390).

`CircuitBreaker` resets its count on a success while closed. The group never
called `record_success()` on a closed circuit, so the count only grew until
someone ran `rebalance()`. The served path is proved in
``tests/integration/test_scattered_health_check_failures_do_not_open_a_group_circuit.py``.
"""

from unittest.mock import MagicMock

from mcp_hangar.domain.model.circuit_breaker import CircuitState
from mcp_hangar.domain.model.mcp_server_group import GroupCircuitClosed, GroupCircuitOpened, McpServerGroup
from mcp_hangar.domain.value_objects import GroupState, ProviderState
from mcp_hangar.domain.events import CircuitBreakerStateChanged


def _server(server_id: str) -> MagicMock:
    server = MagicMock()
    server.id = server_id
    server.mcp_server_id = server_id
    server.state = ProviderState.READY
    server.state_snapshot = ProviderState.READY
    return server


def _group(
    member_ids: list[str], threshold: int = 3, min_healthy: int = 1, unhealthy_threshold: int = 100
) -> McpServerGroup:
    """Members in rotation. By default they stay there: only the circuit is under test."""
    group = McpServerGroup(
        group_id="pool",
        auto_start=False,
        min_healthy=min_healthy,
        unhealthy_threshold=unhealthy_threshold,
        healthy_threshold=1,
        circuit_failure_threshold=threshold,
    )
    for member_id in member_ids:
        group.add_member(_server(member_id))
        group.get_member(member_id).in_rotation = True
    group.collect_events()
    return group


def _replay(group: McpServerGroup, outcomes: list[str], member_id: str = "a") -> None:
    for outcome in outcomes:
        if outcome == "success":
            group.report_success(member_id)
        else:
            group.report_failure(member_id)


def _open(member_ids: list[str], min_healthy: int) -> McpServerGroup:
    """Every member driven out, the last failure opening the circuit."""
    group = _group(member_ids, threshold=len(member_ids), min_healthy=min_healthy, unhealthy_threshold=1)
    for member_id in member_ids:
        group.report_failure(member_id)
    assert group.circuit_open is True and group.healthy_count == 0
    group.collect_events()
    return group


class TestTheThresholdCountsFailuresInARow:
    def test_a_success_between_failures_starts_the_count_again(self):
        group = _group(["a"])

        _replay(group, ["failure", "failure", "success", "failure", "failure"])

        assert group.circuit_open is False
        assert group._circuit_breaker.failure_count == 2
        assert GroupCircuitOpened not in [type(e) for e in group.collect_events()]

    def test_three_failures_in_a_row_open_the_circuit(self):
        group = _group(["a"])

        _replay(group, ["failure", "failure", "success", "failure", "failure", "failure"])

        assert group.circuit_open is True
        assert group.state == GroupState.DEGRADED
        opened = [e for e in group.collect_events() if isinstance(e, GroupCircuitOpened)]
        assert [e.failure_count for e in opened] == [3]

    def test_scattered_failures_never_open_it_however_many(self):
        # Asserted on the events: the sequence ends on a success, which would
        # close an opened circuit again under `min_healthy` and hide it.
        group = _group(["a"])

        _replay(group, ["failure", "failure", "success"] * 20)

        assert GroupCircuitOpened not in [type(e) for e in group.collect_events()]

    def test_a_success_on_another_member_ends_the_run_too(self):
        """The circuit is the group's: it opens when the group fails in a row, not one member."""
        group = _group(["a", "b"])

        for _ in range(5):
            _replay(group, ["failure", "failure"], member_id="a")
            group.report_success("b")

        assert GroupCircuitOpened not in [type(e) for e in group.collect_events()]


class TestAnOpenCircuitClosesOnlyAtMinHealthy:
    def test_a_success_below_min_healthy_leaves_it_open(self):
        group = _open(["a", "b"], min_healthy=2)

        group.report_success("a")

        assert group.get_member("a").in_rotation is True
        assert group.circuit_open is True
        assert group.state == GroupState.DEGRADED
        assert GroupCircuitClosed not in [type(e) for e in group.collect_events()]

    def test_it_closes_once_min_healthy_members_are_back_and_counts_from_nothing(self):
        group = _open(["a", "b"], min_healthy=2)

        group.report_success("a")
        group.report_success("b")

        assert group.circuit_open is False
        assert group.state == GroupState.HEALTHY
        assert [type(e) for e in group.collect_events()].count(GroupCircuitClosed) == 1
        assert group._circuit_breaker.failure_count == 0


class TestHalfOpen:
    def test_the_group_never_half_opens_its_circuit_by_itself(self):
        """The breaker half-opens only in `allow_request()`, which the group does not call."""
        group = _open(["a", "b"], min_healthy=2)
        group._circuit_breaker._opened_at = 0.0  # the reset timeout long past

        group.select_member()
        group.report_failure("a")
        group.report_success("a")

        assert group._circuit_breaker.state is CircuitState.OPEN

    def test_members_coming_back_close_it_straight_from_open(self):
        """No timer (#1398): however long it has been open, the way out is `_maybe_close_circuit()`."""
        group = _open(["a", "b"], min_healthy=2)
        group._circuit_breaker._opened_at = 0.0  # opened long ago

        group.report_success("a")
        assert group._circuit_breaker.state is CircuitState.OPEN
        group.report_success("b")

        assert group._circuit_breaker.state is CircuitState.CLOSED
        changes = [e for e in group.collect_events() if isinstance(e, CircuitBreakerStateChanged)]
        assert [(e.old_state, e.new_state) for e in changes] == [("open", "closed")]
