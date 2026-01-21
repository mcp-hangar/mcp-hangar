"""Provider Group Aggregate - manages a group of providers with load balancing.

A ProviderGroup is an aggregate root that manages multiple Provider instances
as a single logical unit with automatic load balancing and failover.
"""

from dataclasses import dataclass
import threading
import time
from typing import Any, TYPE_CHECKING

from ...logging_config import get_logger
from ..events import DomainEvent
from ..value_objects import GroupId, GroupState, LoadBalancerStrategy, MemberPriority, MemberWeight, ProviderState
from .aggregate import AggregateRoot
from .circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from .load_balancer import LoadBalancer
from .provider import Provider

if TYPE_CHECKING:
    from ...infrastructure.lock_hierarchy import TrackedLock

logger = get_logger(__name__)


# --- Group-specific Domain Events ---


@dataclass
class GroupCreated(DomainEvent):
    """Published when a provider group is created."""

    group_id: str
    strategy: str
    min_healthy: int

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupMemberAdded(DomainEvent):
    """Published when a member is added to a group."""

    group_id: str
    member_id: str
    weight: int
    priority: int

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupMemberRemoved(DomainEvent):
    """Published when a member is removed from a group."""

    group_id: str
    member_id: str

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupMemberHealthChanged(DomainEvent):
    """Published when a member's rotation status changes."""

    group_id: str
    member_id: str
    in_rotation: bool
    reason: str = ""

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupStateChanged(DomainEvent):
    """Published when group state transitions."""

    group_id: str
    old_state: str
    new_state: str
    healthy_count: int
    total_count: int

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupCircuitOpened(DomainEvent):
    """Published when group circuit breaker opens."""

    group_id: str
    failure_count: int

    def __post_init__(self):
        super().__init__()


@dataclass
class GroupCircuitClosed(DomainEvent):
    """Published when group circuit breaker closes."""

    group_id: str

    def __post_init__(self):
        super().__init__()


# --- Group Member ---


@dataclass
class GroupMember:
    """A member of a provider group."""

    provider: Provider
    weight: int = 1
    priority: int = 1
    in_rotation: bool = False  # Currently accepting traffic
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_selected_at: float = 0.0

    @property
    def id(self) -> str:
        """Get member's provider ID as string."""
        # provider.id returns str (from Provider class)
        return str(self.provider.id)


# --- Provider Group Aggregate ---


class ProviderGroup(AggregateRoot):
    """
    Aggregate root for a group of load-balanced providers.

    Responsibilities:
    - Manage member lifecycle
    - Load balancing decisions
    - Group-level health tracking
    - Circuit breaker for the entire group

    Thread-safety:
    - All public methods are thread-safe
    - Internal lock prevents concurrent modification
    """

    def __init__(
        self,
        group_id: str,
        strategy: LoadBalancerStrategy = LoadBalancerStrategy.ROUND_ROBIN,
        min_healthy: int = 1,
        auto_start: bool = True,
        unhealthy_threshold: int = 2,
        healthy_threshold: int = 1,
        circuit_failure_threshold: int = 10,
        circuit_reset_timeout_s: float = 60.0,
        description: str | None = None,
    ):
        """
        Initialize a provider group.

        Args:
            group_id: Unique identifier for the group
            strategy: Load balancing strategy
            min_healthy: Minimum healthy members for HEALTHY state
            auto_start: Automatically start members when added
            unhealthy_threshold: Failures before removing from rotation
            healthy_threshold: Successes before adding back to rotation
            circuit_failure_threshold: Failures before circuit opens
            circuit_reset_timeout_s: Time before circuit resets
            description: Human-readable description
        """
        super().__init__()

        # Identity
        self._id = GroupId(group_id)
        self._description = description

        # Configuration
        self._strategy = strategy
        self._min_healthy = max(1, min_healthy)
        self._auto_start = auto_start
        self._unhealthy_threshold = max(1, unhealthy_threshold)
        self._healthy_threshold = max(1, healthy_threshold)

        # State
        self._state = GroupState.INACTIVE
        self._members: dict[str, GroupMember] = {}
        self._load_balancer = LoadBalancer(strategy)

        # Circuit breaker (extracted for SRP)
        self._circuit_breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=circuit_failure_threshold,
                reset_timeout_s=circuit_reset_timeout_s,
            )
        )

        # Threading
        # Lock hierarchy level: PROVIDER_GROUP (11)
        # Safe to acquire after: PROVIDER (but avoid holding both)
        # Safe to acquire before: EVENT_BUS, EVENT_STORE, STDIO_CLIENT
        self._lock = self._create_lock(group_id)

        self._record_event(
            GroupCreated(
                group_id=group_id,
                strategy=strategy.value,
                min_healthy=min_healthy,
            )
        )

    @staticmethod
    def _create_lock(group_id: str) -> "TrackedLock | threading.RLock":
        """Create lock with hierarchy tracking."""
        try:
            from ...infrastructure.lock_hierarchy import LockLevel, TrackedLock

            return TrackedLock(LockLevel.PROVIDER_GROUP, f"ProviderGroup:{group_id}")
        except ImportError:
            return threading.RLock()

    # --- Properties ---

    @property
    def id(self) -> str:
        """Get group ID."""
        return self._id.value

    @property
    def description(self) -> str | None:
        """Get group description."""
        return self._description

    @property
    def state(self) -> GroupState:
        """Get current group state."""
        with self._lock:
            return self._state

    @property
    def strategy(self) -> LoadBalancerStrategy:
        """Get load balancing strategy."""
        return self._strategy

    @property
    def healthy_count(self) -> int:
        """Number of members currently in rotation."""
        with self._lock:
            return sum(1 for m in self._members.values() if m.in_rotation)

    @property
    def total_count(self) -> int:
        """Total number of members in the group."""
        with self._lock:
            return len(self._members)

    @property
    def is_available(self) -> bool:
        """Can the group accept requests?"""
        with self._lock:
            return not self._circuit_breaker.is_open and self._state.can_accept_requests and self.healthy_count >= 1

    @property
    def circuit_open(self) -> bool:
        """Is the circuit breaker open?"""
        return self._circuit_breaker.is_open

    @property
    def members(self) -> list[GroupMember]:
        """Get list of all members."""
        with self._lock:
            return list(self._members.values())

    # --- Member Management ---

    def add_member(
        self,
        provider: Provider,
        weight: int = 1,
        priority: int = 1,
    ) -> None:
        """
        Add a provider to the group.

        Args:
            provider: Provider instance to add
            weight: Load balancing weight (higher = more traffic)
            priority: Priority for priority-based selection (lower = higher priority)

        Raises:
            ValueError: If member already exists in group
        """
        with self._lock:
            # Get member ID as string for dictionary key
            member_id = str(provider.id)

            if member_id in self._members:
                raise ValueError(f"Member {member_id} already in group {self.id}")

            # Validate weight and priority
            validated_weight = MemberWeight(weight)
            validated_priority = MemberPriority(priority)

            member = GroupMember(
                provider=provider,
                weight=validated_weight.value,
                priority=validated_priority.value,
            )
            self._members[member_id] = member

            self._record_event(
                GroupMemberAdded(
                    group_id=self.id,
                    member_id=member_id,
                    weight=weight,
                    priority=priority,
                )
            )

            logger.info(f"Added member {member_id} to group {self.id} (weight={weight}, priority={priority})")

            # Auto-start if configured
            if self._auto_start:
                self._try_start_member(member)

    def remove_member(self, member_id: str) -> bool:
        """
        Remove a provider from the group.

        Args:
            member_id: ID of the member to remove

        Returns:
            True if member was removed, False if not found
        """
        with self._lock:
            member = self._members.pop(member_id, None)
            if member:
                member.in_rotation = False
                self._update_state()
                self._record_event(
                    GroupMemberRemoved(
                        group_id=self.id,
                        member_id=member_id,
                    )
                )
                logger.info(f"Removed member {member_id} from group {self.id}")
                return True
            return False

    def get_member(self, member_id: str) -> GroupMember | None:
        """Get a member by ID."""
        with self._lock:
            return self._members.get(member_id)

    def _try_start_member(self, member: GroupMember) -> bool:
        """
        Try to start a member and add to rotation if successful.

        Returns:
            True if member started and added to rotation
        """
        try:
            member.provider.ensure_ready()
            if member.provider.state == ProviderState.READY:
                member.in_rotation = True
                member.consecutive_failures = 0
                member.consecutive_successes = 1
                self._update_state()
                self._record_event(
                    GroupMemberHealthChanged(
                        group_id=self.id,
                        member_id=member.id,
                        in_rotation=True,
                        reason="started",
                    )
                )
                logger.info(f"Member {member.id} started and added to rotation")
                return True
        except Exception as e:
            logger.warning(f"Failed to start member {member.id}: {e}")
            member.in_rotation = False
        return False

    # --- Load Balancing ---

    def select_member(self) -> Provider | None:
        """
        Select a member for the next request using load balancer.

        Returns:
            Selected provider or None if no healthy members available
        """
        with self._lock:
            if not self._circuit_breaker.allow_request():
                return None

            self._check_circuit_recovery()

            available = [m for m in self._members.values() if m.in_rotation]
            if not available:
                return None

            selected = self._load_balancer.select(available)
            if selected:
                selected.last_selected_at = time.time()
                return selected.provider

            return None

    def _check_circuit_recovery(self) -> None:
        """Check if circuit just recovered and emit event."""
        if not self._circuit_breaker.is_open and self._state == GroupState.DEGRADED:
            self._record_event(GroupCircuitClosed(group_id=self.id))
            logger.info(f"Circuit breaker closed for group {self.id}")
            self._update_state()

    # --- Health Reporting ---

    def report_success(self, member_id: str) -> None:
        """
        Report successful invocation for a member.

        Args:
            member_id: ID of the member that succeeded
        """
        with self._lock:
            member = self._members.get(member_id)
            if not member:
                return

            member.consecutive_failures = 0
            member.consecutive_successes += 1
            self._maybe_add_to_rotation(member, member_id)

    def _maybe_add_to_rotation(self, member: GroupMember, member_id: str) -> None:
        """Add member back to rotation if healthy threshold reached."""
        if member.in_rotation:
            return
        if member.provider.state != ProviderState.READY:
            return
        if member.consecutive_successes < self._healthy_threshold:
            return

        member.in_rotation = True
        self._record_event(
            GroupMemberHealthChanged(
                group_id=self.id,
                member_id=member_id,
                in_rotation=True,
                reason="healthy_threshold_reached",
            )
        )
        self._update_state()
        logger.info(f"Member {member_id} added back to rotation")

    def report_failure(self, member_id: str) -> None:
        """
        Report failed invocation for a member.

        Args:
            member_id: ID of the member that failed
        """
        with self._lock:
            member = self._members.get(member_id)
            if not member:
                return

            member.consecutive_failures += 1
            member.consecutive_successes = 0

            self._maybe_remove_from_rotation(member, member_id)
            self._maybe_open_circuit()
            self._update_state()

    def _maybe_remove_from_rotation(self, member: GroupMember, member_id: str) -> None:
        """Remove member from rotation if unhealthy threshold reached."""
        if member.consecutive_failures < self._unhealthy_threshold:
            return
        if not member.in_rotation:
            return

        member.in_rotation = False
        self._record_event(
            GroupMemberHealthChanged(
                group_id=self.id,
                member_id=member_id,
                in_rotation=False,
                reason="unhealthy_threshold_reached",
            )
        )
        logger.info(f"Member {member_id} removed from rotation after {member.consecutive_failures} failures")

    def _maybe_open_circuit(self) -> None:
        """Open circuit breaker if failure threshold reached."""
        circuit_just_opened = self._circuit_breaker.record_failure()
        if not circuit_just_opened:
            return

        self._record_event(
            GroupCircuitOpened(
                group_id=self.id,
                failure_count=self._circuit_breaker.failure_count,
            )
        )
        logger.warning(
            f"Circuit breaker opened for group {self.id} after {self._circuit_breaker.failure_count} failures"
        )

    # --- State Management ---

    def _update_state(self) -> None:
        """Update group state based on member health."""
        old_state = self._state
        healthy = self.healthy_count
        total = len(self._members)

        if self._circuit_breaker.is_open:
            new_state = GroupState.DEGRADED
        elif healthy == 0:
            new_state = GroupState.INACTIVE
        elif healthy < self._min_healthy:
            new_state = GroupState.PARTIAL
        else:
            new_state = GroupState.HEALTHY

        if new_state != old_state:
            self._state = new_state
            self._record_event(
                GroupStateChanged(
                    group_id=self.id,
                    old_state=old_state.value,
                    new_state=new_state.value,
                    healthy_count=healthy,
                    total_count=total,
                )
            )
            logger.info(f"Group {self.id} state: {old_state.value} -> {new_state.value} (healthy={healthy}/{total})")

    def rebalance(self) -> None:
        """
        Manually trigger rebalancing.

        Re-evaluates health of all members and updates rotation.
        """
        with self._lock:
            for member in self._members.values():
                if member.provider.state == ProviderState.READY:
                    if not member.in_rotation:
                        member.in_rotation = True
                        member.consecutive_failures = 0
                        self._record_event(
                            GroupMemberHealthChanged(
                                group_id=self.id,
                                member_id=member.id,
                                in_rotation=True,
                                reason="rebalance",
                            )
                        )
                else:
                    if member.in_rotation:
                        member.in_rotation = False
                        self._record_event(
                            GroupMemberHealthChanged(
                                group_id=self.id,
                                member_id=member.id,
                                in_rotation=False,
                                reason="rebalance",
                            )
                        )

            # Reset load balancer state
            self._load_balancer.reset()

            # Reset circuit breaker
            was_open = self._circuit_breaker.is_open
            self._circuit_breaker.reset()
            if was_open:
                self._record_event(GroupCircuitClosed(group_id=self.id))

            self._update_state()
            logger.info(f"Group {self.id} rebalanced: {self.healthy_count} healthy")

    # --- Lifecycle ---

    def start_all(self) -> int:
        """
        Start all members.

        Returns:
            Number of members successfully started
        """
        with self._lock:
            started = 0
            for member in self._members.values():
                if self._try_start_member(member):
                    started += 1
            return started

    def stop_all(self) -> None:
        """Stop all members."""
        with self._lock:
            for member in self._members.values():
                try:
                    member.provider.shutdown()
                    member.in_rotation = False
                except Exception as e:
                    logger.warning(f"Failed to stop member {member.id}: {e}")
            self._update_state()

    def shutdown(self) -> None:
        """Shutdown the group and all members."""
        self.stop_all()
        logger.info(f"Group {self.id} shutdown complete")

    # --- Tools Access ---

    def get_tools(self) -> list[Any]:
        """
        Get tools from a healthy member.

        Returns tools from the first healthy member, as all members
        should have the same tools.
        """
        with self._lock:
            for member in self._members.values():
                if member.in_rotation and member.provider.state == ProviderState.READY:
                    return list(member.provider.tools)
            return []

    def get_tool_names(self) -> list[str]:
        """Get list of tool names from a healthy member."""
        with self._lock:
            for member in self._members.values():
                if member.in_rotation and member.provider.state == ProviderState.READY:
                    return member.provider.get_tool_names()
            return []

    # --- Serialization ---

    def to_status_dict(self) -> dict[str, Any]:
        """Get status as dictionary."""
        with self._lock:
            return {
                "group_id": self.id,
                "description": self._description,
                "state": self._state.value,
                "strategy": self._strategy.value,
                "min_healthy": self._min_healthy,
                "healthy_count": self.healthy_count,
                "total_members": len(self._members),
                "is_available": self.is_available,
                "circuit_open": self._circuit_breaker.is_open,
                "members": [
                    {
                        "id": m.id,
                        "state": m.provider.state.value,
                        "in_rotation": m.in_rotation,
                        "weight": m.weight,
                        "priority": m.priority,
                        "consecutive_failures": m.consecutive_failures,
                    }
                    for m in self._members.values()
                ],
            }
