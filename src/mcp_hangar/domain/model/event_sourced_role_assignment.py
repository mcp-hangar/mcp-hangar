"""Event Sourced Role Assignment aggregate.

Implements Event Sourcing pattern for role assignments where:
- State is derived from events, not stored directly
- All changes are captured as immutable events
- State can be rebuilt by replaying events
"""

from dataclasses import dataclass
from typing import Any

from ..events import DomainEvent, RoleAssigned, RoleRevoked
from .aggregate import AggregateRoot


@dataclass
class RoleAssignmentSnapshot:
    """Snapshot of principal's role assignments.

    Attributes:
        principal_id: Principal ID.
        assignments: Dict of scope -> set of role names.
        version: Aggregate version.
    """

    principal_id: str
    assignments: dict[str, list[str]]
    version: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "principal_id": self.principal_id,
            "assignments": self.assignments,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RoleAssignmentSnapshot":
        """Create from dictionary."""
        return cls(
            principal_id=d["principal_id"],
            assignments=d.get("assignments", {}),
            version=d.get("version", 0),
        )


class EventSourcedRoleAssignment(AggregateRoot):
    """Event Sourced Role Assignment aggregate.

    Tracks all role assignments for a single principal.
    All changes are recorded as events.
    """

    def __init__(self, principal_id: str):
        """Initialize role assignment aggregate.

        Args:
            principal_id: Principal whose roles are tracked.
        """
        super().__init__()

        self._principal_id = principal_id
        # scope -> set of role names
        self._assignments: dict[str, set[str]] = {}

    @property
    def principal_id(self) -> str:
        return self._principal_id

    # =========================================================================
    # Factory Methods
    # =========================================================================

    @classmethod
    def from_events(
        cls,
        principal_id: str,
        events: list[DomainEvent],
    ) -> "EventSourcedRoleAssignment":
        """Rebuild role assignment state from events.

        Args:
            principal_id: Principal ID.
            events: Events to replay.

        Returns:
            EventSourcedRoleAssignment with state rebuilt from events.
        """
        assignment = cls(principal_id)

        for event in events:
            assignment._apply_event(event)

        return assignment

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RoleAssignmentSnapshot,
        events: list[DomainEvent] | None = None,
    ) -> "EventSourcedRoleAssignment":
        """Load from snapshot and optional subsequent events.

        Args:
            snapshot: Snapshot to load from.
            events: Optional events after snapshot.

        Returns:
            EventSourcedRoleAssignment with state from snapshot + events.
        """
        assignment = cls(snapshot.principal_id)

        # Restore state from snapshot
        assignment._assignments = {scope: set(roles) for scope, roles in snapshot.assignments.items()}
        assignment._version = snapshot.version

        # Apply any events after snapshot
        if events:
            for event in events:
                assignment._apply_event(event)

        return assignment

    # =========================================================================
    # Commands (mutate state via events)
    # =========================================================================

    def assign_role(
        self,
        role_name: str,
        scope: str = "global",
        assigned_by: str = "system",
    ) -> bool:
        """Assign a role to this principal.

        Args:
            role_name: Name of the role to assign.
            scope: Scope of the assignment.
            assigned_by: Who is assigning the role.

        Returns:
            True if role was assigned, False if already assigned.
        """
        # Check if already assigned
        if scope in self._assignments and role_name in self._assignments[scope]:
            return False

        self._record_event(
            RoleAssigned(
                principal_id=self._principal_id,
                role_name=role_name,
                scope=scope,
                assigned_by=assigned_by,
            )
        )

        # Apply immediately
        if scope not in self._assignments:
            self._assignments[scope] = set()
        self._assignments[scope].add(role_name)

        return True

    def revoke_role(
        self,
        role_name: str,
        scope: str = "global",
        revoked_by: str = "system",
    ) -> bool:
        """Revoke a role from this principal.

        Args:
            role_name: Name of the role to revoke.
            scope: Scope from which to revoke.
            revoked_by: Who is revoking the role.

        Returns:
            True if role was revoked, False if not assigned.
        """
        # Check if assigned
        if scope not in self._assignments or role_name not in self._assignments[scope]:
            return False

        self._record_event(
            RoleRevoked(
                principal_id=self._principal_id,
                role_name=role_name,
                scope=scope,
                revoked_by=revoked_by,
            )
        )

        # Apply immediately
        self._assignments[scope].discard(role_name)
        if not self._assignments[scope]:
            del self._assignments[scope]

        return True

    # =========================================================================
    # Event Application
    # =========================================================================

    def _apply_event(self, event: DomainEvent) -> None:
        """Apply an event to update state.

        This is called when replaying events to rebuild state.
        """
        if isinstance(event, RoleAssigned):
            scope = event.scope
            if scope not in self._assignments:
                self._assignments[scope] = set()
            self._assignments[scope].add(event.role_name)

        elif isinstance(event, RoleRevoked):
            scope = event.scope
            if scope in self._assignments:
                self._assignments[scope].discard(event.role_name)
                if not self._assignments[scope]:
                    del self._assignments[scope]

        self._version += 1

    # =========================================================================
    # Queries
    # =========================================================================

    def get_role_names(self, scope: str = "*") -> set[str]:
        """Get all role names for this principal.

        Args:
            scope: Scope to filter by, or "*" for all scopes.

        Returns:
            Set of role names.
        """
        if scope == "*":
            # All roles across all scopes
            result = set()
            for roles in self._assignments.values():
                result.update(roles)
            return result
        else:
            # Specific scope + global
            result = set(self._assignments.get(scope, set()))
            result.update(self._assignments.get("global", set()))
            return result

    def has_role(self, role_name: str, scope: str = "*") -> bool:
        """Check if principal has a specific role.

        Args:
            role_name: Role to check.
            scope: Scope to check in.

        Returns:
            True if principal has the role.
        """
        return role_name in self.get_role_names(scope)

    def create_snapshot(self) -> RoleAssignmentSnapshot:
        """Create a snapshot of current state."""
        return RoleAssignmentSnapshot(
            principal_id=self._principal_id,
            assignments={scope: list(roles) for scope, roles in self._assignments.items()},
            version=self._version,
        )
