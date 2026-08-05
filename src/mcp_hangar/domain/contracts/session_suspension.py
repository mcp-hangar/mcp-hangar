"""Session suspension contract.

Suspending a session is an enforcement decision -- a detection rule matches and
the offending session stops being served. The decision belongs to the
application layer; where the suspension is *recorded* is an infrastructure
concern, and today that is a bounded in-memory cache.

Before this contract existed, the enforcement handler reached for the record
directly::

    from ...server.api.sessions import _suspended_sessions
    _suspended_sessions.add(session_id)

which put an application handler behind a function-local import of another
module's private, delivery-layer state. The import-contract ledger recorded it
as the only application -> delivery edge in the tree.

Implementations are provided by the infrastructure layer.
"""

from typing import Protocol


class ISessionSuspensionRegistry(Protocol):
    """Records which sessions are currently suspended.

    Implementations are expected to be safe to call from multiple threads: the
    HTTP routes and the event-bus handler both reach it, and they do not share
    a thread.
    """

    def suspend(self, session_id: str) -> None:
        """Mark a session suspended. Re-suspending an already-suspended session
        is not an error -- an implementation with a TTL should treat it as a
        refresh rather than a no-op."""
        ...

    def unsuspend(self, session_id: str) -> None:
        """Lift a suspension. Unsuspending an unknown session is not an error."""
        ...

    def is_suspended(self, session_id: str) -> bool:
        """Whether the session is currently suspended."""
        ...
