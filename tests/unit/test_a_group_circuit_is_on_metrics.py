"""Each replica exposes its own view of every group's circuit (#1357).

The served path, meaning a real ``bootstrap()``, calls through the app
``serve --http`` serves, and a scrape of its ``/metrics``, is proved in
``tests/integration/test_each_replica_exposes_its_group_circuit.py``.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain.model.mcp_server_group import GroupCreated, GroupDeleted, McpServerGroup
from mcp_hangar.domain.services import reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ProviderState
from mcp_hangar.group_recovery import GroupRecoveryWorker
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.server import config as server_config
from mcp_hangar.server.bootstrap.composition import GROUPS
from mcp_hangar.server.bootstrap.group_circuit_metric import observe_created_group, observe_group_circuit
from mcp_hangar.server.config import load_config
from mcp_hangar.server.state import get_runtime

GROUP = "unit-circuit-pool"
METRIC = "mcp_hangar_group_circuit_open"


def _server(server_id: str) -> MagicMock:
    server = MagicMock()
    server.id = server_id
    server.mcp_server_id = server_id
    server.state = ProviderState.READY
    server.state_snapshot = ProviderState.READY
    return server


def _group() -> McpServerGroup:
    """Two members in rotation. One failure takes a member out; two in a row open the circuit."""
    group = McpServerGroup(
        group_id=GROUP,
        auto_start=False,
        min_healthy=1,
        unhealthy_threshold=1,
        healthy_threshold=1,
        circuit_failure_threshold=2,
    )
    for member_id in ("a", "b"):
        group.add_member(_server(member_id))
        group.get_member(member_id).in_rotation = True
    group.collect_events()
    return group


def _trip(group: McpServerGroup) -> None:
    group.report_failure("a")
    group.report_failure("b")
    assert group.circuit_open is True


def _exposed() -> list[str]:
    return [line for line in prometheus_metrics.get_metrics().splitlines() if line.startswith(f"{METRIC}{{")]


def _gauge() -> list[str]:
    return [line for line in _exposed() if f'group="{GROUP}"' in line]


@pytest.fixture(autouse=True)
def no_group_left_behind():
    yield
    GROUPS.pop(GROUP, None)
    prometheus_metrics.remove_group_series(GROUP)


@pytest.fixture
def heard() -> tuple[McpServerGroup, list[bool]]:
    group = _group()
    states: list[bool] = []
    group.observe_circuit(states.append)
    return group, states


@pytest.fixture
def served() -> McpServerGroup:
    """Loaded on this replica, the way config loading at bootstrap leaves it."""
    group = _group()
    GROUPS[GROUP] = group
    observe_group_circuit(group)
    return group


class TestTheGroupTellsItsListenerEveryTransition:
    def test_it_is_told_the_state_when_it_starts_listening(self, heard):
        _group_, states = heard

        assert states == [False]

    def test_failures_in_a_row_open_it_and_a_member_back_at_min_healthy_closes_it(self, heard):
        group, states = heard

        _trip(group)
        group.report_success("a")

        assert states == [False, True, False]

    def test_rebalance_closes_it(self, heard):
        group, states = heard
        _trip(group)

        group.rebalance()

        assert states == [False, True, False]


class TestTheGauge:
    def test_it_is_registered_as_a_gauge(self):
        assert prometheus_metrics.REGISTRY.get(METRIC) is prometheus_metrics.GROUP_CIRCUIT_OPEN
        assert f"# TYPE {METRIC} gauge" in prometheus_metrics.get_metrics().splitlines()

    def test_a_loaded_group_is_on_the_scrape_before_its_first_transition(self, served):
        assert _gauge() == [f'{METRIC}{{group="{GROUP}"}} 0.0']

    def test_its_only_label_is_the_group(self, served):
        _trip(served)

        assert prometheus_metrics.GROUP_CIRCUIT_OPEN.label_names == ["group"]
        assert _gauge() == [f'{METRIC}{{group="{GROUP}"}} 1.0']

    def test_it_follows_the_circuit_both_ways(self, served):
        _trip(served)
        opened = _gauge()
        served.report_success("a")

        assert opened == [f'{METRIC}{{group="{GROUP}"}} 1.0']
        assert _gauge() == [f'{METRIC}{{group="{GROUP}"}} 0.0']

    def test_a_deleted_group_leaves_the_scrape(self, served):
        _trip(served)

        MetricsEventHandler().handle(GroupDeleted(group_id=GROUP))

        assert _gauge() == []

    def test_a_group_no_longer_served_does_not_write_its_series_back(self, served):
        GROUPS.pop(GROUP)
        MetricsEventHandler().handle(GroupDeleted(group_id=GROUP))

        _trip(served)

        assert _gauge() == []

    def test_a_replaced_group_does_not_write_over_its_successor(self, served):
        successor = _group()
        GROUPS[GROUP] = successor
        observe_group_circuit(successor)

        _trip(served)

        assert _gauge() == [f'{METRIC}{{group="{GROUP}"}} 0.0']


class TestAGroupCreatedThroughTheApi:
    def test_goes_on_the_scrape(self):
        GROUPS[GROUP] = _group()

        observe_created_group(GroupCreated(group_id=GROUP, strategy="round_robin", min_healthy=1))

        assert _gauge() == [f'{METRIC}{{group="{GROUP}"}} 0.0']

    def test_one_this_replica_does_not_serve_does_not(self):
        observe_created_group(GroupCreated(group_id=GROUP, strategy="round_robin", min_healthy=1))

        assert _gauge() == []


KEPT = "unit-circuit-kept"
#: The member ids the reload tests below build, so teardown removes exactly those.
MEMBER_IDS = ("unit-circuit-m1", "unit-circuit-m2", "unit-circuit-k1", "unit-circuit-k2")


def _group_spec(*member_ids: str) -> dict[str, Any]:
    """Two failures in a row open the circuit, as `_group` does."""
    return {
        "mode": "group",
        "auto_start": False,
        "health": {"unhealthy_threshold": 1, "healthy_threshold": 1},
        "circuit_breaker": {"failure_threshold": 2},
        "members": [
            {"id": member_id, "mode": "subprocess", "command": ["python", "-m", "some.server"]}
            for member_id in member_ids
        ],
    }


BOTH = {
    GROUP: _group_spec("unit-circuit-m1", "unit-circuit-m2"),
    KEPT: _group_spec("unit-circuit-k1", "unit-circuit-k2"),
}
KEPT_ONLY = {KEPT: BOTH[KEPT]}


def _trip_loaded(group_id: str) -> None:
    _trip_loaded_object(GROUPS[group_id])


def _trip_loaded_object(group: McpServerGroup) -> None:
    for member in group.members:
        member.in_rotation = True
    for member in list(group.members):
        group.report_failure(member.id)
    assert group.circuit_open is True


def _series(group_id: str) -> list[str]:
    return [line for line in _exposed() if f'group="{group_id}"' in line]


def _reset_config() -> None:
    reset_tool_access_resolver()
    reset_tool_projection_registry()
    server_config._BUILT_FROM.clear()
    repository = get_runtime().repository
    for mcp_server_id in MEMBER_IDS:
        if repository.exists(mcp_server_id):
            repository.remove(mcp_server_id)


class TestAReloadThatRemovesAGroup:
    """A reload removes a group without `GroupDeleted`, and must drop its series all the same (#1564)."""

    @pytest.fixture(autouse=True)
    def loaded(self):
        original_groups = dict(GROUPS)
        GROUPS.clear()
        _reset_config()
        load_config(BOTH)
        yield
        _reset_config()
        GROUPS.clear()
        GROUPS.update(original_groups)
        prometheus_metrics.remove_group_series(KEPT)

    def test_drops_its_series_when_its_circuit_is_open(self):
        _trip_loaded(GROUP)
        assert _series(GROUP) == [f'{METRIC}{{group="{GROUP}"}} 1.0']

        load_config(KEPT_ONLY, replace=True)

        assert GROUP not in GROUPS
        assert _series(GROUP) == []

    def test_a_late_transition_of_the_removed_group_does_not_bring_it_back(self):
        removed = GROUPS[GROUP]

        load_config(KEPT_ONLY, replace=True)
        _trip_loaded_object(removed)

        assert _series(GROUP) == []

    def test_keeps_the_series_and_the_listener_of_a_group_that_stays(self):
        load_config(KEPT_ONLY, replace=True)

        assert _series(KEPT) == [f'{METRIC}{{group="{KEPT}"}} 0.0']
        _trip_loaded(KEPT)
        assert _series(KEPT) == [f'{METRIC}{{group="{KEPT}"}} 1.0']

    def test_the_removed_group_is_not_probed_for_recovery(self):
        removed = GROUPS[GROUP]
        load_config(KEPT_ONLY, replace=True)
        worker = GroupRecoveryWorker(GROUPS, event_bus=MagicMock())
        worker.running = True

        with patch.object(removed, "recovery_candidates", return_value=[]) as consulted:
            worker.probe_once()

        consulted.assert_not_called()
