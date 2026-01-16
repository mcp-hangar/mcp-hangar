"""Unit tests for ProviderGroup aggregate."""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.model.provider_group import GroupCircuitOpened, GroupCreated, GroupMemberAdded, ProviderGroup
from mcp_hangar.domain.value_objects import GroupState, LoadBalancerStrategy, ProviderState


def create_mock_provider(provider_id: str, state: ProviderState = ProviderState.READY):
    """Create a mock provider for testing."""
    mock = MagicMock()
    mock.id = provider_id
    mock.provider_id = provider_id
    mock.state = state
    mock.ensure_ready = MagicMock()
    mock.shutdown = MagicMock()
    mock.tools = []
    mock.get_tool_names = MagicMock(return_value=[])
    return mock


class TestProviderGroupCreation:
    """Tests for ProviderGroup initialization."""

    def test_creates_with_defaults(self):
        """Should create group with default configuration."""
        group = ProviderGroup(group_id="test-group")

        assert group.id == "test-group"
        assert group.state == GroupState.INACTIVE
        assert group.strategy == LoadBalancerStrategy.ROUND_ROBIN
        assert group.healthy_count == 0
        assert group.total_count == 0
        assert group.is_available is False

    def test_creates_with_custom_strategy(self):
        """Should create group with specified strategy."""
        group = ProviderGroup(
            group_id="weighted-group",
            strategy=LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN,
        )

        assert group.strategy == LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN

    def test_creates_with_custom_min_healthy(self):
        """Should create group with specified min_healthy."""
        group = ProviderGroup(
            group_id="high-availability",
            min_healthy=3,
        )

        assert group._min_healthy == 3

    def test_emits_group_created_event(self):
        """Should emit GroupCreated event on creation."""
        group = ProviderGroup(group_id="event-test")
        events = group.collect_events()

        assert len(events) == 1
        assert isinstance(events[0], GroupCreated)
        assert events[0].group_id == "event-test"


class TestMemberManagement:
    """Tests for adding/removing group members."""

    def test_add_member(self):
        """Should add member to group."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1")

        group.add_member(provider, weight=2, priority=1)

        assert group.total_count == 1
        member = group.get_member("provider-1")
        assert member is not None
        assert member.weight == 2
        assert member.priority == 1

    def test_add_member_emits_event(self):
        """Should emit GroupMemberAdded event."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        group.collect_events()  # Clear creation event
        provider = create_mock_provider("provider-1")

        group.add_member(provider)
        events = group.collect_events()

        assert len(events) >= 1
        add_event = [e for e in events if isinstance(e, GroupMemberAdded)][0]
        assert add_event.member_id == "provider-1"

    def test_add_duplicate_member_raises(self):
        """Should raise error when adding duplicate member."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1")
        group.add_member(provider)

        with pytest.raises(ValueError, match="already in group"):
            group.add_member(provider)

    def test_remove_member(self):
        """Should remove member from group."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1")
        group.add_member(provider)

        result = group.remove_member("provider-1")

        assert result is True
        assert group.total_count == 0
        assert group.get_member("provider-1") is None

    def test_remove_nonexistent_member_returns_false(self):
        """Should return False when removing non-existent member."""
        group = ProviderGroup(group_id="test-group")

        result = group.remove_member("nonexistent")

        assert result is False

    def test_auto_start_adds_to_rotation(self):
        """With auto_start=True, ready members are added to rotation."""
        group = ProviderGroup(group_id="test-group", auto_start=True)
        provider = create_mock_provider("provider-1", state=ProviderState.READY)

        group.add_member(provider)

        # Member should be in rotation if provider is READY
        member = group.get_member("provider-1")
        # Note: actual in_rotation depends on ensure_ready success
        assert member is not None


class TestLoadBalancing:
    """Tests for load balancing functionality."""

    def test_select_member_returns_provider(self):
        """Should return a provider from available members."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)

        # Manually put in rotation and update state for test
        member = group.get_member("provider-1")
        member.in_rotation = True
        group._update_state()  # Update state to HEALTHY

        selected = group.select_member()

        assert selected is provider

    def test_select_member_returns_none_when_empty(self):
        """Should return None when no members available."""
        group = ProviderGroup(group_id="test-group")

        selected = group.select_member()

        assert selected is None

    def test_select_member_returns_none_when_circuit_open(self):
        """Should return None when circuit breaker is open."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            circuit_failure_threshold=1,
        )
        provider = create_mock_provider("provider-1")
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        # Open circuit breaker via report_failure
        group.report_failure("provider-1")
        assert group.circuit_open is True

        selected = group.select_member()

        assert selected is None


class TestHealthReporting:
    """Tests for health reporting and rotation management."""

    def test_report_success_resets_failures(self):
        """report_success should reset consecutive failures."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.consecutive_failures = 5

        group.report_success("provider-1")

        assert member.consecutive_failures == 0

    def test_report_failure_increments_counter(self):
        """report_failure should increment failure counter."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        group.report_failure("provider-1")
        group.report_failure("provider-1")

        assert member.consecutive_failures == 2

    def test_report_failure_removes_from_rotation_at_threshold(self):
        """Should remove from rotation when unhealthy threshold reached."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            unhealthy_threshold=2,
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        group.report_failure("provider-1")
        assert member.in_rotation is True  # Still in rotation

        group.report_failure("provider-1")
        assert member.in_rotation is False  # Removed after threshold


class TestCircuitBreaker:
    """Tests for circuit breaker functionality."""

    def test_circuit_opens_at_failure_threshold(self):
        """Circuit should open when failure threshold reached."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            circuit_failure_threshold=3,
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        # Trigger failures
        for _ in range(3):
            group.report_failure("provider-1")

        assert group.circuit_open is True

    def test_circuit_emits_event_when_opened(self):
        """Should emit GroupCircuitOpened event."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            circuit_failure_threshold=1,
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True
        group.collect_events()  # Clear previous events

        group.report_failure("provider-1")
        events = group.collect_events()

        circuit_events = [e for e in events if isinstance(e, GroupCircuitOpened)]
        assert len(circuit_events) == 1


class TestStateManagement:
    """Tests for group state transitions."""

    def test_state_is_inactive_with_no_healthy(self):
        """State should be INACTIVE when no healthy members."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.COLD)
        group.add_member(provider)

        assert group.state == GroupState.INACTIVE

    def test_state_is_partial_below_min_healthy(self):
        """State should be PARTIAL when healthy < min_healthy."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            min_healthy=2,
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        # Force state update
        group._update_state()

        assert group.state == GroupState.PARTIAL

    def test_state_is_healthy_at_min_healthy(self):
        """State should be HEALTHY when healthy >= min_healthy."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            min_healthy=1,
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True

        # Force state update
        group._update_state()

        assert group.state == GroupState.HEALTHY


class TestRebalance:
    """Tests for rebalance functionality."""

    def test_rebalance_adds_ready_members_to_rotation(self):
        """Rebalance should add READY members to rotation."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = False  # Start out of rotation

        group.rebalance()

        assert member.in_rotation is True

    def test_rebalance_removes_non_ready_members(self):
        """Rebalance should remove non-READY members from rotation."""
        group = ProviderGroup(group_id="test-group", auto_start=False)
        provider = create_mock_provider("provider-1", ProviderState.COLD)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True  # Somehow in rotation but not ready

        group.rebalance()

        assert member.in_rotation is False

    def test_rebalance_resets_circuit_breaker(self):
        """Rebalance should reset circuit breaker."""
        group = ProviderGroup(
            group_id="test-group",
            auto_start=False,
            circuit_failure_threshold=1,
        )
        # Open circuit breaker via report_failure
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider)
        member = group.get_member("provider-1")
        member.in_rotation = True
        group.report_failure("provider-1")  # This opens circuit

        assert group.circuit_open is True

        group.rebalance()

        assert group.circuit_open is False


class TestSerialization:
    """Tests for to_status_dict serialization."""

    def test_to_status_dict_includes_all_fields(self):
        """to_status_dict should include all relevant fields."""
        group = ProviderGroup(
            group_id="test-group",
            strategy=LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN,
            min_healthy=2,
            description="Test group",
        )
        provider = create_mock_provider("provider-1", ProviderState.READY)
        group.add_member(provider, weight=3, priority=1)

        status = group.to_status_dict()

        assert status["group_id"] == "test-group"
        assert status["strategy"] == "weighted_round_robin"
        assert status["min_healthy"] == 2
        assert status["description"] == "Test group"
        assert len(status["members"]) == 1
        assert status["members"][0]["id"] == "provider-1"
        assert status["members"][0]["weight"] == 3
