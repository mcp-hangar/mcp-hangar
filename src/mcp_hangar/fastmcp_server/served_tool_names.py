"""What each caller was last served by ``tools/list``, so a stale name can say so (#1368).

A client keeps the tool list it was served. Once the projection can change under
a connected client (#1365), that client will call a name that has since left it,
and until now the answer was the ``-32601`` a name that never existed gets. The
client could only retry or give up. This module remembers what each caller was
served, so the front door can tell that one case apart and answer it with a
reason: the list is out of date, list again.

Denied and absent stay indistinguishable (#905)
-----------------------------------------------
The memory holds only what THIS caller was served, and the reason says only
"your list is out of date". So:

* A name this caller was never served gets the ordinary ``-32601``, byte for
  byte the error it got before this module existed. That holds whether the name
  belongs to another tenant, is denied to this caller by policy, or exists
  nowhere. Existence is never consulted.
* The reason is a constant. It carries no upstream id, no tool name and no
  count, and it does not say why the name left. A withdrawal for this tenant, a
  withdrawal for everyone, a policy edit and an upstream that went away all
  read the same.
* All the caller learns is that it was once served the name, and it already
  knows that.

What is remembered, and under which key
---------------------------------------
The names in the last real ``tools/list`` answered for an identity, governed and
management tools alike. The key is the tenant plus the principal (type, user and
agent), narrowed by the caller's session when it has one.

The session is ``CallerIdentity.session_id``: the ``sid`` claim of a verified
token, or an ``x-session-id`` from a trusted proxy. It is the same session
suspension keys on, and a caller cannot choose it. The transport's
``Mcp-Session-Id`` is not used. The served app is stateless (#877), so the
transport never issues one, and on the 2026-07-28 transport SEP-2567 removed
sessions altogether.

The tenant and the principal are always part of the key. A session narrows an
entry and never widens it, so an entry cannot answer for another identity.

Without a session the key is the tenant and the principal. That is the usual
case, and it is the stateless one. Two clients of one principal then share one
entry, and the later listing wins. The worst that does is give a client of that
same principal the reason for a name that principal was served, and listing
again is still the right advice for it. Nothing crosses an identity.

A caller without a tenant is never remembered. In ``front_door`` it is served
nothing (the fail-closed no-identity projection), so there is nothing to
remember, and it never gets a reason.

Timing
------
An entry is recorded when a real listing is answered. The SDK's pre-dispatch
``tools/list`` on a ``tools/call`` (#1049) is not recorded: the client never
received it. An empty listing clears the entry, because whatever the caller held
before is no longer what it holds.

Bounded and per replica
-----------------------
An LRU of at most :data:`MAX_IDENTITIES` identities, holding name sets only.
The names are the projection's own strings, so an entry costs the set and not
copies of the names. An evicted identity gets the ordinary ``-32601``.

The memory lives in the process. A call that reaches a replica which did not
serve the caller's list gets the ordinary ``-32601``, which is what every such
call got before. Without affinity a client loses the hint on some calls and
nothing else (the same per-replica family as #877). Both failure modes fall
back to the old behaviour, and neither can produce a reason for a name the
caller was not served.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable

from ..context import get_identity_context

#: The one reason a stale name's ``-32601`` carries. Part of the wire contract:
#: a client matches on it, so it does not change.
PROJECTION_CHANGED = "projection_changed"

#: How many identities are remembered at once, least recently used evicted first.
MAX_IDENTITIES = 1024

#: ``(tenant, principal type, user, agent, session)``.
ServedKey = tuple[str, str, str | None, str | None, str | None]


def projection_changed_error_data() -> dict[str, str]:
    """The machine-readable payload that rides with a stale name's ``-32601``.

    It follows the convention of ``tasks_wire.missing_capability_error_data``:
    the code says what failed, and ``data`` tells the client what to do about
    it. Here that is to list again. The payload is a constant, so it cannot
    carry anything about the name, the upstream or anyone else's surface.
    """
    return {"reason": PROJECTION_CHANGED}


class ServedNames:
    """Bounded, thread-safe memory of the names each identity was last served."""

    def __init__(self, max_identities: int = MAX_IDENTITIES) -> None:
        if max_identities < 1:
            raise ValueError("max_identities must be at least 1")
        self._max_identities = max_identities
        self._lock = threading.Lock()
        self._served: OrderedDict[ServedKey, frozenset[str]] = OrderedDict()

    def remember(self, key: ServedKey, names: Iterable[str]) -> None:
        """Replace what *key* holds with *names*, the listing it was just served."""
        served = frozenset(names)
        with self._lock:
            if not served:
                self._served.pop(key, None)
                return
            self._served[key] = served
            self._served.move_to_end(key)
            while len(self._served) > self._max_identities:
                self._served.popitem(last=False)

    def was_served(self, key: ServedKey, name: str) -> bool:
        """Was *name* in the last listing *key* was served?"""
        with self._lock:
            served = self._served.get(key)
            if served is None:
                return False
            self._served.move_to_end(key)
            return name in served

    def __len__(self) -> int:
        with self._lock:
            return len(self._served)


#: This replica's memory. Read through the module at call time, so a test can
#: swap in a fresh one.
SERVED = ServedNames()


def served_key() -> ServedKey | None:
    """The identity the bound caller's listing is remembered under, or None when it has no tenant."""
    identity = get_identity_context()
    caller = identity.caller if identity is not None else None
    tenant_id = caller.tenant_id if caller is not None else None
    if caller is None or not tenant_id:
        return None
    return (tenant_id, caller.principal_type, caller.user_id, caller.agent_id, caller.session_id)


def remember_served(names: Iterable[str]) -> None:
    """Record the listing the bound caller was just served."""
    key = served_key()
    if key is not None:
        SERVED.remember(key, names)


def was_served_to_caller(name: str) -> bool:
    """Was *name* in the last listing the bound caller was served on this replica?"""
    key = served_key()
    return key is not None and SERVED.was_served(key, name)
