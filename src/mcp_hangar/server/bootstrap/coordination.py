"""Wiring the management lease into the process that has to hold it.

One holder, created here and asked from three places: the discovery loop, the
TTL expiry loop and the metric snapshot worker. It is a module-level holder for
the same reason the storage backend is -- those callers are constructed at
different points in bootstrap, and threading a keeper through all of them would
mean changing five signatures to deliver one boolean.

**Without a storage backend there is no keeper, and `may_manage` is simply
True.** That is a standalone gateway, which is every deployment that has not
opted into `persistence.backend`, and it manages its own fleet exactly as it
did before this existed.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...application.services.lease_keeper import ManagementLeaseKeeper
from ...domain.events import current_instance_id
from ...logging_config import get_logger
from .composition import get_persistence_backend

if TYPE_CHECKING:
    from ...application.services.event_tailer import EventTailer

logger = get_logger(__name__)

_keeper: ManagementLeaseKeeper | None = None


def init_lease_keeper(config: dict[str, Any] | None = None) -> ManagementLeaseKeeper | None:
    """Create the keeper, if the selected backend can hold a lease.

    Does not start it: bootstrap assembles, lifecycle starts. Starting here
    would have this instance holding the lease -- and so managing -- before the
    loops it gates even exist.

    Args:
        config: Full configuration. `coordination.lease_ttl_s` and
            `coordination.renew_interval_s` override the defaults.

    Returns:
        The keeper, or None when there is nothing to coordinate through.
    """
    global _keeper

    from ...infrastructure.launchers import set_local_mode_policy

    backend = get_persistence_backend()
    if backend is None:
        logger.info(
            "management_lease_absent",
            detail="no storage backend selected; this gateway manages its own fleet, as a standalone one does",
        )
        _keeper = None
        # Explicitly permissive. A standalone gateway runs every mode, and it
        # must not inherit a policy left behind by a previous bootstrap in the
        # same process -- which is what a test suite is.
        set_local_mode_policy(None)
        return None

    coordination = (config or {}).get("coordination", {})
    kwargs: dict[str, Any] = {}
    if "lease_ttl_s" in coordination:
        kwargs["ttl_s"] = float(coordination["lease_ttl_s"])
    if "renew_interval_s" in coordination:
        kwargs["interval_s"] = float(coordination["renew_interval_s"])
    if "renew_deadline_s" in coordination:
        kwargs["renew_deadline_s"] = float(coordination["renew_deadline_s"])

    _keeper = ManagementLeaseKeeper(backend.management_lease(), current_instance_id(), **kwargs)

    # Local-mode servers are the lease holder's to run: they are child processes
    # of one gateway, so a follower starting its own copy makes a second server
    # rather than a second route to the first (#790, phase 4.1). Set here rather
    # than at each launch site, because `get_launcher` is the one place every
    # launch goes through.
    set_local_mode_policy(may_manage)
    return _keeper


def get_lease_keeper() -> ManagementLeaseKeeper | None:
    """The keeper for this process, if there is one."""
    return _keeper


def may_manage() -> bool:
    """Whether this instance may run the fleet-management loops right now.

    Called per cycle by everything it gates. True without a keeper: a standalone
    gateway is always its own manager, and a gateway that has not selected a
    storage backend has no peers to disagree with.
    """
    keeper = _keeper
    return True if keeper is None else keeper.may_manage()


_tailer: EventTailer | None = None


def init_event_tailer(runtime: Any) -> EventTailer | None:
    """Create the tailer, **capturing the log head before the fleet is read**.

    Placement is the whole contract of this function. The cursor is taken when
    the tailer is constructed, so this must be called before
    `restore_persisted_fleet`: head first, then snapshot, and an event landing
    between the two is delivered rather than falling in the gap between "not in
    the snapshot yet" and "before my cursor". The other order loses it silently.

    Does not start it -- bootstrap assembles, lifecycle starts. A replica that
    began applying its peers' events before its own handlers were registered
    would deliver them to an empty table.

    Returns None when there is no shared log to follow: no storage backend
    means no peers, and a store that keeps nothing has nothing to read back.
    """
    global _tailer

    from ...application.services.event_tailer import EventTailer
    from ...domain.events import current_instance_id

    if get_persistence_backend() is None:
        _tailer = None
        return None

    store = getattr(getattr(runtime, "event_bus", None), "event_store", None)
    if store is None or not getattr(store, "can_replay", False):
        logger.info(
            "event_tailer_absent",
            detail="the event store keeps nothing, so there is no shared log to follow",
        )
        _tailer = None
        return None

    _tailer = EventTailer(store, runtime.event_bus, current_instance_id())
    return _tailer


def get_event_tailer() -> EventTailer | None:
    """The tailer for this process, if there is one."""
    return _tailer
