"""What a served front-door listing weighs, what it is made of, and whether it changed (#1369).

`mcp_hangar_projected_tools` (#912) counted the tools a client was handed and
split them by kind. It could not say how many bytes that was, which upstream
they came from, or whether the list a caller holds is still the list it would be
served now, and that last question is the subject of #1365. The 2026-09-10
diagnosis needed a probe from outside to learn that the gateway held 46 tools
and 174 KB while a connected client held none.

Everything here is written by one call, :func:`observe_served_listing`, on a
listing the client received. The SDK's pre-dispatch ``tools/list`` on a
``tools/call`` (#1049) is not one: it measures nothing and moves no memory.

Size
----
The bytes of each definition as compact UTF-8 JSON, dumped the way the modern
transport dumps a result (``by_alias``, ``exclude_none``). The JSON-RPC
envelope, the ``_meta`` block and the separators between definitions are left
out: the definitions are the part that grows with the fleet. By kind in
``mcp_hangar_projected_surface_bytes``. Per upstream, governed tools only, in
``mcp_hangar_projected_upstream_bytes``. A group member reads as its group, so a
listing served from either member lands on one series.

Churn
-----
``mcp_hangar_projection_changes_total`` counts listings that served an identity
a :class:`Projection` different from the one this replica last served it.
"Different" is ``Projection`` equality (#1367): the same names routed to the
same upstream tools with the same definitions, in any order. It is compared as a
digest that keeps that equality for everything that reaches the wire, because
keeping the projections would keep every caller's copy of every definition.

The identity is the one a stale name is recognised under (`served_tool_names`):
tenant plus principal, narrowed by session. An empty listing is remembered too,
since going from nothing to 46 tools is the change #1365 is about. These are not
counted:

* An identity's first listing on this replica. There is nothing to compare it to.
* A caller without a tenant. In ``front_door`` it is served nothing, always.
* An identity evicted from the memory (:data:`MAX_IDENTITIES`, least recently
  used first). Its next listing is a first listing. So the counter can miss a
  change, but it never counts one that did not happen.
* A change the caller saw only by being served by another replica. The memory is
  per replica, like the sessions of #877.

Only the governed projection is compared. The ``hangar_*`` management tools are
a per-principal authorization decision the listing appends: their size is
measured, and whether they changed is not a fact about the fleet.

Cardinality
-----------
No tenant label anywhere, for #895's reason: a public front door has unbounded
tenant cardinality. ``kind`` has two values, ``mcp_server`` is the fleet the
operator configured, and the counter has no labels. The memory holds at most
:data:`MAX_IDENTITIES` 32-byte digests.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from .. import metrics as prometheus_metrics
from .served_tool_names import MAX_IDENTITIES, ServedKey, served_key

if TYPE_CHECKING:
    from .flat_tool_projection import Projection

KIND_GOVERNED = "governed"
KIND_MANAGEMENT = "management"

#: Stands in for a missing half of a projection entry in its digest.
_ABSENT = b"\x00"


def definition_bytes(tool: Any) -> bytes:
    """One served definition, as the compact UTF-8 JSON the modern transport sends.

    Keys are sorted so the bytes are canonical. That changes no length, so
    the size is the size on the wire.
    """
    payload = tool.model_dump(mode="json", by_alias=True, exclude_none=True)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def projection_digest(projection: Projection, encoded: Mapping[str, bytes]) -> bytes:
    """A digest that two projections share exactly when they are equal (#1367).

    *encoded* is each governed definition by name, from `definition_bytes`.
    Order is not part of it, because a client keys a listing by name. Every
    field is length-prefixed, so no two different projections hash the same
    stream.
    """
    digest = hashlib.sha256()
    for name in sorted(projection.routes.keys() | encoded.keys()):
        route = projection.routes.get(name)
        parts = (
            name.encode("utf-8"),
            route[0].encode("utf-8") if route else _ABSENT,
            route[1].encode("utf-8") if route else _ABSENT,
            encoded.get(name, _ABSENT),
        )
        for part in parts:
            digest.update(len(part).to_bytes(8, "big"))
            digest.update(part)
    return digest.digest()


class LastServed:
    """Bounded, thread-safe memory of the projection digest each identity was last served."""

    def __init__(self, max_identities: int = MAX_IDENTITIES) -> None:
        if max_identities < 1:
            raise ValueError("max_identities must be at least 1")
        self._max_identities = max_identities
        self._lock = threading.Lock()
        self._digests: OrderedDict[ServedKey, bytes] = OrderedDict()

    def exchange(self, key: ServedKey, digest: bytes) -> bytes | None:
        """Remember *digest* for *key* and return the one it replaced, or ``None`` on a first listing."""
        with self._lock:
            previous = self._digests.pop(key, None)
            self._digests[key] = digest
            while len(self._digests) > self._max_identities:
                self._digests.popitem(last=False)
        return previous

    def __len__(self) -> int:
        with self._lock:
            return len(self._digests)


#: This replica's memory. Read through the module at call time, so a test can
#: swap in a fresh one.
LAST_SERVED = LastServed()


def expose_change_count() -> None:
    """Put the change counter on the exposition at zero before any listing.

    A counter series that first appears at 1 reads as no increase to
    ``increase()``, so the first change a front door served would be
    invisible to the query that asks whether anything changed.
    """
    prometheus_metrics.PROJECTION_CHANGES_TOTAL.inc(0)


def observe_served_listing(projection: Projection, management: Iterable[Any], group_of: Mapping[str, str]) -> None:
    """Measure a listing the client received: its size by kind and by upstream, and whether it changed.

    *group_of* maps a group member's id to its group's, as the call path
    collapses it (#857).
    """
    governed = {name: definition_bytes(tool) for name, tool in projection.tools.items()}
    managed = [definition_bytes(tool) for tool in management]

    prometheus_metrics.PROJECTED_TOOLS.observe(len(governed), kind=KIND_GOVERNED)
    prometheus_metrics.PROJECTED_TOOLS.observe(len(managed), kind=KIND_MANAGEMENT)
    prometheus_metrics.PROJECTED_SURFACE_BYTES.observe(sum(map(len, governed.values())), kind=KIND_GOVERNED)
    prometheus_metrics.PROJECTED_SURFACE_BYTES.observe(sum(map(len, managed)), kind=KIND_MANAGEMENT)
    for upstream, size in _bytes_by_upstream(projection, governed, group_of).items():
        prometheus_metrics.PROJECTED_UPSTREAM_BYTES.observe(size, mcp_server=upstream)
    _count_a_change(projection, governed)


def _bytes_by_upstream(
    projection: Projection, encoded: Mapping[str, bytes], group_of: Mapping[str, str]
) -> dict[str, int]:
    sizes: dict[str, int] = {}
    for name, definition in encoded.items():
        server = projection.routes[name][0]
        upstream = group_of.get(server, server)
        sizes[upstream] = sizes.get(upstream, 0) + len(definition)
    return sizes


def _count_a_change(projection: Projection, encoded: Mapping[str, bytes]) -> None:
    key = served_key()
    if key is None:
        return
    digest = projection_digest(projection, encoded)
    previous = LAST_SERVED.exchange(key, digest)
    if previous is not None and previous != digest:
        prometheus_metrics.PROJECTION_CHANGES_TOTAL.inc()
