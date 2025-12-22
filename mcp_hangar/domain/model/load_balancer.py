"""Load balancing strategies for provider groups.

This module implements various load balancing strategies for distributing
requests across group members.
"""

from abc import ABC, abstractmethod
import random
import threading
from typing import List, Optional, TYPE_CHECKING

from ..value_objects import LoadBalancerStrategy

if TYPE_CHECKING:
    from .provider_group import GroupMember


class BaseStrategy(ABC):
    """Abstract base class for load balancing strategies."""

    @abstractmethod
    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        """
        Select a member from available members.

        Args:
            members: List of available (in_rotation) group members

        Returns:
            Selected member or None if no members available
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset strategy state (e.g., after rebalancing)."""
        pass


class RoundRobinStrategy(BaseStrategy):
    """Simple round-robin selection.

    Each member gets selected in order, cycling through the list.
    """

    def __init__(self):
        self._index = 0
        self._lock = threading.Lock()

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        if not members:
            return None
        with self._lock:
            selected = members[self._index % len(members)]
            self._index += 1
        return selected

    def reset(self) -> None:
        with self._lock:
            self._index = 0


class WeightedRoundRobinStrategy(BaseStrategy):
    """
    Weighted round-robin using smooth weighted algorithm.

    Members with higher weights get proportionally more requests.
    Uses the Nginx smooth weighted round-robin algorithm for even distribution.
    """

    def __init__(self):
        self._current_weights: dict = {}
        self._lock = threading.Lock()

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        if not members:
            return None

        with self._lock:
            total_weight = sum(m.weight for m in members)

            # Initialize or update current weights
            for m in members:
                mid = id(m)
                if mid not in self._current_weights:
                    self._current_weights[mid] = 0
                self._current_weights[mid] += m.weight

            # Select member with highest current weight
            best = max(members, key=lambda m: self._current_weights.get(id(m), 0))

            # Reduce selected member's weight by total weight
            self._current_weights[id(best)] -= total_weight

            return best

    def reset(self) -> None:
        with self._lock:
            self._current_weights.clear()


class LeastConnectionsStrategy(BaseStrategy):
    """Select member with fewest recent selections.

    Uses last_selected_at timestamp as a proxy for "connections".
    Prefers members that haven't been selected recently.
    """

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        if not members:
            return None
        # Select member with oldest last_selected_at (least recently used)
        return min(members, key=lambda m: m.last_selected_at)

    def reset(self) -> None:
        # No state to reset
        pass


class RandomStrategy(BaseStrategy):
    """Random selection with optional weights.

    Uses weighted random selection where higher weight = higher probability.
    """

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        if not members:
            return None
        weights = [m.weight for m in members]
        return random.choices(members, weights=weights, k=1)[0]

    def reset(self) -> None:
        # No state to reset
        pass


class PriorityStrategy(BaseStrategy):
    """Always select lowest priority member that's healthy.

    Priority 1 is highest priority. Falls back to next priority if 1 is unavailable.
    Useful for primary/backup scenarios.
    """

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        if not members:
            return None
        # Select member with lowest priority number (highest priority)
        return min(members, key=lambda m: m.priority)

    def reset(self) -> None:
        # No state to reset
        pass


class LoadBalancer:
    """
    Load balancer that selects members based on configured strategy.

    Thread-safe - each strategy implementation handles its own locking.
    """

    _STRATEGIES = {
        LoadBalancerStrategy.ROUND_ROBIN: RoundRobinStrategy,
        LoadBalancerStrategy.WEIGHTED_ROUND_ROBIN: WeightedRoundRobinStrategy,
        LoadBalancerStrategy.LEAST_CONNECTIONS: LeastConnectionsStrategy,
        LoadBalancerStrategy.RANDOM: RandomStrategy,
        LoadBalancerStrategy.PRIORITY: PriorityStrategy,
    }

    def __init__(self, strategy: LoadBalancerStrategy):
        self._strategy_type = strategy
        self._impl = self._STRATEGIES[strategy]()

    @property
    def strategy(self) -> LoadBalancerStrategy:
        """Get the current strategy type."""
        return self._strategy_type

    def select(self, members: List["GroupMember"]) -> Optional["GroupMember"]:
        """Select a member from available members."""
        return self._impl.select(members)

    def reset(self) -> None:
        """Reset strategy state."""
        self._impl.reset()
