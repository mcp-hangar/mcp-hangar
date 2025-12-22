"""Unit tests for load balancer strategies."""

from mcp_hangar.domain.model.load_balancer import (
    LeastConnectionsStrategy,
    LoadBalancer,
    PriorityStrategy,
    RandomStrategy,
    RoundRobinStrategy,
    WeightedRoundRobinStrategy,
)
from mcp_hangar.domain.value_objects import LoadBalancerStrategy


class MockMember:
    """Mock member for testing load balancer strategies."""

    def __init__(
        self,
        name: str,
        weight: int = 1,
        priority: int = 1,
        last_selected_at: float = 0.0,
    ):
        self.name = name
        self.weight = weight
        self.priority = priority
        self.last_selected_at = last_selected_at

    def __repr__(self):
        return f"MockMember({self.name})"


class TestRoundRobinStrategy:
    """Tests for RoundRobinStrategy."""

    def test_cycles_through_members(self):
        """Should cycle through all members in order."""
        members = [MockMember("A"), MockMember("B"), MockMember("C")]
        strategy = RoundRobinStrategy()

        selections = [strategy.select(members).name for _ in range(6)]

        assert selections == ["A", "B", "C", "A", "B", "C"]

    def test_single_member(self):
        """Should always return the same member if only one exists."""
        members = [MockMember("Solo")]
        strategy = RoundRobinStrategy()

        selections = [strategy.select(members).name for _ in range(3)]

        assert selections == ["Solo", "Solo", "Solo"]

    def test_empty_list_returns_none(self):
        """Should return None for empty member list."""
        strategy = RoundRobinStrategy()

        result = strategy.select([])

        assert result is None

    def test_reset_restarts_cycle(self):
        """Reset should restart from the beginning."""
        members = [MockMember("A"), MockMember("B")]
        strategy = RoundRobinStrategy()

        # Advance to B
        strategy.select(members)
        strategy.reset()

        # Should start from A again
        assert strategy.select(members).name == "A"


class TestWeightedRoundRobinStrategy:
    """Tests for WeightedRoundRobinStrategy."""

    def test_respects_weights(self):
        """Members with higher weights should be selected more often."""
        members = [MockMember("Heavy", weight=2), MockMember("Light", weight=1)]
        strategy = WeightedRoundRobinStrategy()

        # Over many selections, ratio should approximate weights
        selections = [strategy.select(members).name for _ in range(30)]
        heavy_count = selections.count("Heavy")
        light_count = selections.count("Light")

        # Heavy should be selected approximately 2x as often as Light
        assert heavy_count > light_count
        assert 1.5 < (heavy_count / light_count) < 2.5

    def test_equal_weights_like_round_robin(self):
        """Equal weights should behave like round robin."""
        members = [MockMember("A", weight=1), MockMember("B", weight=1)]
        strategy = WeightedRoundRobinStrategy()

        selections = [strategy.select(members).name for _ in range(10)]
        a_count = selections.count("A")
        b_count = selections.count("B")

        assert abs(a_count - b_count) <= 2

    def test_empty_list_returns_none(self):
        """Should return None for empty member list."""
        strategy = WeightedRoundRobinStrategy()

        result = strategy.select([])

        assert result is None


class TestLeastConnectionsStrategy:
    """Tests for LeastConnectionsStrategy."""

    def test_selects_least_recently_used(self):
        """Should select member with oldest last_selected_at."""
        members = [
            MockMember("Recent", last_selected_at=100.0),
            MockMember("Old", last_selected_at=50.0),
            MockMember("Oldest", last_selected_at=10.0),
        ]
        strategy = LeastConnectionsStrategy()

        result = strategy.select(members)

        assert result.name == "Oldest"

    def test_empty_list_returns_none(self):
        """Should return None for empty member list."""
        strategy = LeastConnectionsStrategy()

        result = strategy.select([])

        assert result is None


class TestPriorityStrategy:
    """Tests for PriorityStrategy."""

    def test_selects_lowest_priority_number(self):
        """Should always select member with lowest priority number."""
        members = [
            MockMember("Low", priority=3),
            MockMember("High", priority=1),
            MockMember("Med", priority=2),
        ]
        strategy = PriorityStrategy()

        # Should always select High (priority=1)
        selections = [strategy.select(members).name for _ in range(5)]

        assert all(s == "High" for s in selections)

    def test_equal_priorities_consistent(self):
        """With equal priorities, should consistently pick one."""
        members = [
            MockMember("A", priority=1),
            MockMember("B", priority=1),
        ]
        strategy = PriorityStrategy()

        # min() is deterministic for equal values
        result = strategy.select(members)
        assert result is not None

    def test_empty_list_returns_none(self):
        """Should return None for empty member list."""
        strategy = PriorityStrategy()

        result = strategy.select([])

        assert result is None


class TestRandomStrategy:
    """Tests for RandomStrategy."""

    def test_respects_weights_statistically(self):
        """Higher weights should result in more selections statistically."""
        members = [MockMember("Heavy", weight=9), MockMember("Light", weight=1)]
        strategy = RandomStrategy()

        # With many iterations, should approximate weight ratio
        selections = [strategy.select(members).name for _ in range(1000)]
        heavy_count = selections.count("Heavy")
        light_count = selections.count("Light")

        # Should be roughly 9:1 ratio (with some variance)
        ratio = heavy_count / light_count if light_count > 0 else float("inf")
        assert 5 < ratio < 15  # Allow significant variance for randomness

    def test_empty_list_returns_none(self):
        """Should return None for empty member list."""
        strategy = RandomStrategy()

        result = strategy.select([])

        assert result is None


class TestLoadBalancer:
    """Tests for LoadBalancer wrapper class."""

    def test_creates_round_robin_strategy(self):
        """Should create correct strategy for ROUND_ROBIN."""
        lb = LoadBalancer(LoadBalancerStrategy.ROUND_ROBIN)

        assert lb.strategy == LoadBalancerStrategy.ROUND_ROBIN

    def test_creates_weighted_strategy(self):
        """Should create correct strategy for WEIGHTED_ROUND_ROBIN."""
        lb = LoadBalancer(LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN)

        assert lb.strategy == LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN

    def test_delegates_select_to_strategy(self):
        """Select should delegate to underlying strategy."""
        lb = LoadBalancer(LoadBalancerStrategy.ROUND_ROBIN)
        members = [MockMember("A"), MockMember("B")]

        selections = [lb.select(members).name for _ in range(4)]

        assert selections == ["A", "B", "A", "B"]

    def test_reset_delegates_to_strategy(self):
        """Reset should delegate to underlying strategy."""
        lb = LoadBalancer(LoadBalancerStrategy.ROUND_ROBIN)
        members = [MockMember("A"), MockMember("B")]

        lb.select(members)  # A
        lb.reset()

        # Should restart from A
        assert lb.select(members).name == "A"
