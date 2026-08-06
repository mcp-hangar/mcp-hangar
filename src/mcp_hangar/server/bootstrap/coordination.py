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

from typing import Any

from ...application.services.lease_keeper import ManagementLeaseKeeper
from ...domain.events import current_instance_id
from ...logging_config import get_logger
from .composition import get_persistence_backend

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

    backend = get_persistence_backend()
    if backend is None:
        logger.info(
            "management_lease_absent",
            detail="no storage backend selected; this gateway manages its own fleet, as a standalone one does",
        )
        _keeper = None
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
