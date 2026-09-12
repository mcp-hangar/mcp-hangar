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

What a suspension is keyed on
-----------------------------
A suspension names a session id, and a call is refused only when the caller it
comes from carries that id (GHSA-fhwh-fmq2-7m5c). A served caller carries one
from exactly two sources: the ``sid`` claim of a verified bearer token, or an
``x-session-id`` header from a trusted proxy. A caller with neither carries no
session id and nothing can match it, so suspension does not apply to it.
"""

import re
from typing import Protocol, TypeGuard

#: The session ids a suspension can name: what ``POST /api/sessions/{id}/suspend``
#: accepts. A session id outside this shape can never match a suspension, so it
#: is dropped rather than carried on the caller's identity, where it would reach
#: audit records as caller-chosen text.
_SESSION_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]{1,128}")

#: The ``Principal.metadata`` key under which an authenticator records the
#: session id of a credential it verified (a bearer token's ``sid`` claim). The
#: identity bridge prefers it to any header, because the caller cannot change it.
VERIFIED_SESSION_ID_KEY = "verified_session_id"


def is_well_formed_session_id(value: object) -> TypeGuard[str]:
    """Whether *value* is a session id a suspension could name."""
    return isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) is not None


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
