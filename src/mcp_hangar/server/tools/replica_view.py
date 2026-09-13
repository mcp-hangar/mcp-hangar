"""What one replica knows about the fleet, read once for every tool that reports it (#1380).

`hangar_status` and `hangar_health` answer the same question -- which servers
and groups exist, and in what state -- and they used to answer it separately.
`hangar_status` counted hot-loaded servers and `hangar_health` did not, so one
replica could report 3 servers through one tool and 2 through the other. Both
now render from `observe_replica()`, so from one replica they cannot disagree
about the fleet.

**The answer is replica-local, and it says so.** Under session affinity an
operator does not pick the replica that answers. Two calls a minute apart can
reach two pods, one 35 seconds old with every server ready and one 80 minutes
old with most of them cold. Unless each response names the replica that
answered, the reader takes the difference between two pods for a change in the
fleet. So every response carries:

- `replica.instance_id` -- the identity minted at bootstrap
  (`HANGAR_INSTANCE_LABEL` or the hostname, plus a per-process suffix). It is
  the same value the management lease names as `holder`, the peer event tailer
  skips as its own, events carry as `produced_by`, traces carry as
  `service.instance.id`, and `GET /system` reports as `instance.instance_id`.
  One replica has one name everywhere.
- `replica.uptime_seconds` -- how long *this process* has run. Uptime is a
  property of a pod, not of the fleet.
- `scope: "replica"` and `scope_note`, which say it in words.

Nothing here asks peers or reads shared state. The fleet view belongs to
Prometheus, which already scrapes every replica; shared breaker and rotation
state is #1358 and is not a reporting concern.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from ...domain.events import current_instance_id
from ...infrastructure.runtime_store import LoadMetadata
from ..context import get_context

#: When this process started, as far as the tools can tell: the import of this
#: module, which happens while the server registers its tools. It used to live
#: in `hangar.py`; it moved here so every tool reports one uptime.
_PROCESS_STARTED_AT: float = time.time()

#: Value of `scope` on a replica-local answer. A string rather than a boolean,
#: so that a fleet-wide answer, if one is ever added, can name itself.
REPLICA_SCOPE = "replica"

#: Plain-words version of `scope`, for a reader who does not know the field.
SCOPE_NOTE = (
    "This describes what the replica named in replica.instance_id knows, not the fleet. "
    "Other replicas can report different state and uptime at the same moment. "
    "For a fleet-wide view, query the per-replica metrics in Prometheus."
)


@dataclass(frozen=True)
class ConfiguredServer:
    """One configured (repository) server as this replica sees it."""

    mcp_server_id: str
    state: str
    mode: str


@dataclass(frozen=True)
class HotLoadedServer:
    """One hot-loaded server as this replica sees it."""

    mcp_server_id: str
    state: str
    metadata: LoadMetadata


@dataclass(frozen=True)
class GroupView:
    """One group as this replica sees it. `healthy_count` is the group's own (#1356)."""

    group_id: str
    state: str
    healthy_count: int
    total_count: int


@dataclass(frozen=True)
class ReplicaView:
    """One read of this replica's servers and groups, and which replica took it."""

    instance_id: str
    uptime_seconds: float
    configured: tuple[ConfiguredServer, ...]
    hot_loaded: tuple[HotLoadedServer, ...]
    groups: tuple[GroupView, ...]

    @property
    def server_states(self) -> list[str]:
        """The state of every server this replica runs, configured and hot-loaded."""
        return [s.state for s in self.configured] + [s.state for s in self.hot_loaded]

    @property
    def total_servers(self) -> int:
        """Number of servers this replica runs, configured and hot-loaded."""
        return len(self.configured) + len(self.hot_loaded)

    @property
    def ready_servers(self) -> int:
        """Number of those servers in the `ready` state."""
        return sum(1 for state in self.server_states if state == "ready")

    def servers_by_state(self) -> dict[str, int]:
        """Count of servers per state, over the same set as `total_servers`."""
        counts: dict[str, int] = {}
        for state in self.server_states:
            counts[state] = counts.get(state, 0) + 1
        return counts

    def replica_block(self) -> dict[str, Any]:
        """The `replica` field: which replica answered, and for how long it has run."""
        return {
            "instance_id": self.instance_id,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "uptime": format_uptime(self.uptime_seconds),
        }

    def scope_fields(self) -> dict[str, Any]:
        """The fields every replica-local response carries: `replica`, `scope`, `scope_note`."""
        return {
            "replica": self.replica_block(),
            "scope": REPLICA_SCOPE,
            "scope_note": SCOPE_NOTE,
        }


def observe_replica() -> ReplicaView:
    """Read this replica's servers, hot-loaded servers and groups, once.

    The only place either status tool reads fleet state from, so a change to
    what counts as "a server" reaches both tools or neither.

    Configured servers come from the repository itself, not through
    `ListMcpServersQuery`. That handler reads the same repository, but going
    through the bus would make `hangar_health` fail wherever the query handlers
    are not registered, which is exactly where an operator reaches for a
    health check. `hangar_health` has always read the repository directly;
    `hangar_status` now does too.
    """
    from ..state import get_runtime_mcp_servers

    ctx = get_context()
    configured = tuple(
        ConfiguredServer(mcp_server_id=mcp_server_id, state=server.state.value, mode=server.mode.value)
        for mcp_server_id, server in ctx.repository.get_all().items()
    )
    hot_loaded = tuple(
        HotLoadedServer(
            mcp_server_id=str(server.mcp_server_id),
            state=server.state.value if hasattr(server, "state") else "unknown",
            metadata=metadata,
        )
        for server, metadata in get_runtime_mcp_servers().list_all()
    )
    groups = tuple(
        GroupView(
            group_id=group_id,
            state=group.state.value,
            healthy_count=group.healthy_count,
            total_count=group.total_count,
        )
        for group_id, group in ctx.groups.items()
    )
    return ReplicaView(
        instance_id=current_instance_id(),
        uptime_seconds=time.time() - _PROCESS_STARTED_AT,
        configured=configured,
        hot_loaded=hot_loaded,
        groups=groups,
    )


def format_uptime(seconds: float) -> str:
    """Format uptime as a human-readable string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
