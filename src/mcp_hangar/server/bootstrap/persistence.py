"""The one storage decision, taken once, at the top.

```yaml
persistence:
  backend: postgresql        # sqlite | postgresql
  postgresql:
    host: db.internal.example
    port: 5432
    database: mcp_hangar
    user: hangar
    password: ${PGPASSWORD}
```

`backend` names one bundle and the gateway persists everything through it. The
block under the backend's own name is handed to its factory untouched, so
`data_dir` means nothing to PostgreSQL and `host` means nothing to SQLite, and
neither has to know the other's vocabulary.

**Either one or the other.** Storage used to be decided in two independent
places -- `auth.storage.driver` and `event_store.driver` -- so a deployment could
keep API keys in PostgreSQL and its event log in a local file, and nothing
compared them. When `persistence.backend` is set, a legacy key that disagrees
with it is a startup refusal rather than a silent mixture.

Omitting `persistence` entirely keeps the previous per-subsystem behaviour
exactly as it was. That is deliberate: 2.4.0 is released, and a storage rewiring
must not change what an existing configuration does.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.infrastructure.persistence.registry import PersistenceBackend, create_backend
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

#: Long enough for a fleet of a few hundred, short enough that a wedged database
#: fails the boot rather than hanging it forever.
RESTORE_TIMEOUT_S = 60.0


class ConflictingStorageConfigurationError(ValueError):
    """A per-subsystem driver contradicts the selected backend.

    Refused rather than resolved by precedence. Whichever way a precedence rule
    fell, half the configuration would be silently ignored -- and the half that
    loses is the one the operator wrote most recently, which is the one they are
    most sure about.
    """

    def __init__(self, backend: str, conflicts: list[tuple[str, str]]) -> None:
        self.backend = backend
        self.conflicts = conflicts
        detail = "; ".join(f"{key} is {value!r}" for key, value in conflicts)
        super().__init__(
            f"persistence.backend is {backend!r}, but {detail}. "
            "Storage is one decision: remove the per-subsystem driver keys, or "
            "remove persistence.backend and keep configuring subsystems individually."
        )


#: Which backend a legacy driver value implies. `memory` is deliberately absent:
#: it is a testing choice rather than a storage backend, so it never conflicts
#: with a selection and stays available under the legacy keys.
_IMPLIED_BACKEND: dict[str, str] = {
    "sqlite": "sqlite",
    "postgresql": "postgresql",
    "postgres": "postgresql",
}

#: The per-subsystem keys that used to decide storage on their own, as paths
#: into the configuration. One list, so adding a subsystem means one edit rather
#: than remembering to update a check somewhere else.
_LEGACY_DRIVER_PATHS: tuple[tuple[str, ...], ...] = (
    ("auth", "storage", "driver"),
    ("event_store", "driver"),
)


def _read_path(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = config
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _legacy_conflicts(backend: str, full_config: dict[str, Any]) -> list[tuple[str, str]]:
    """Legacy driver settings that name a different backend than the one selected."""
    conflicts: list[tuple[str, str]] = []
    for path in _LEGACY_DRIVER_PATHS:
        value = _read_path(full_config, path)
        if not isinstance(value, str):
            continue
        implied = _IMPLIED_BACKEND.get(value.lower())
        if implied is not None and implied != backend:
            conflicts.append((".".join(path), value))
    return conflicts


def select_backend(full_config: dict[str, Any]) -> PersistenceBackend | None:
    """Build the one backend this process persists through, if one is configured.

    Args:
        full_config: The whole configuration mapping.

    Returns:
        The backend, or None when `persistence.backend` is absent -- in which
        case every subsystem keeps configuring its own storage as before.

    Raises:
        ConflictingStorageConfigurationError: if a legacy per-subsystem driver
            names a different backend than the one selected.
        UnknownPersistenceBackendError: if the name has no factory.
        IncompletePersistenceBackendError: if the backend does not serve every
            concern the gateway persists.
    """
    persistence_config = full_config.get("persistence") or {}
    name = persistence_config.get("backend")
    if not name:
        return None

    name = str(name).lower()
    conflicts = _legacy_conflicts(name, full_config)
    if conflicts:
        raise ConflictingStorageConfigurationError(name, conflicts)

    backend_config = persistence_config.get(name) or {}
    backend = create_backend(name, dict(backend_config))
    logger.info(
        "persistence_backend_ready",
        backend=name,
        detail="every persisted concern is served by this backend",
    )
    return backend


class ClusterNeedsSharedStorageError(RuntimeError):
    """A hangar cluster was configured on storage the replicas cannot share.

    Raised at startup, because the failure it prevents does not look like one.
    Several gateways on a file-backed backend each get their own file, each
    grant themselves their own lease -- the SQLite adapter always grants,
    correctly, because a file admits one writer -- each run the management loops
    and each hold their own fleet. They never disagree, because they cannot see
    each other. Every health check is green and the deployment has as many
    fleets as it has pods.

    A warning would be read once, in the logs of a gateway that appeared to be
    working. This is a place where refusing to start is the smaller outage.
    """

    def __init__(self, backend: str) -> None:
        super().__init__(
            f"this gateway is configured as part of a cluster (`coordination:`), and the '{backend}' storage "
            "backend is local to one process. Replicas that cannot share storage are not a cluster: each "
            "would hold its own fleet and its own lease, and they would never notice each other. Use "
            "`persistence.backend: postgresql`, or remove the `coordination:` block to run this as a "
            "single gateway."
        )


class ClusterNeedsASelectedBackendError(RuntimeError):
    """A hangar cluster was declared without deciding where its replicas persist.

    The same fleet-per-pod outcome as the refusal above, reached by never being
    asked the question. With no `persistence.backend` there is no backend to
    take a lease through, so bootstrap creates no lease keeper and `may_manage`
    is True in every process: each replica runs the management loops, each holds
    its own fleet, and each reports `manages_fleet: true`.

    Reachable while the replicas do share one database. The legacy per-subsystem
    keys -- `event_store.driver: postgresql`, `auth.storage.driver: postgresql`
    -- still configure storage on their own, and a deployment on those has
    selected no backend at all, so nothing in it decides which storage the
    replicas coordinate through.
    """

    def __init__(self) -> None:
        super().__init__(
            "this gateway is configured as part of a cluster (`coordination:`), and no storage backend has "
            "been selected. Replicas coordinate through the backend named by `persistence.backend`, so "
            "without one there is no lease to take: every replica would manage the fleet and none would "
            "notice the others. Set `persistence.backend: postgresql`, or remove the `coordination:` block "
            "to run this as a single gateway."
        )


class LocalModeInDeclaredClusterError(RuntimeError):
    """A declared cluster carries a server it can only run on one replica.

    `subprocess`, `docker` and `container` attach a child process's stdio to
    **one** gateway. Registering such a server through the API is already
    refused where storage is shareable, and launching one on a follower is
    refused again -- but a server declared in `config.yaml` goes through
    neither path. It is simply loaded, on every replica, and then only the
    lease holder can start it.

    What an operator sees from that is not an error. It is a fleet where
    `GET /api/mcp_servers/<id>/tools` answers with five tools on one pod and an
    empty list on the others, and a `409` from whichever replica the load
    balancer happened to pick. Measured on two replicas sharing one database.

    Asked on the axis the operator controls: a `coordination:` block is the
    statement that these replicas are meant to be **one** gateway, and in that
    deployment a child-process server is a configuration error rather than a
    surprise to discover at call time. Without the block -- a single gateway
    that merely happens to use PostgreSQL -- nothing here fires and the server
    runs as it always has.
    """

    def __init__(self, offenders: list[tuple[str, str]]) -> None:
        self.offenders = offenders
        detail = "; ".join(f"{server_id!r} is {mode!r}" for server_id, mode in offenders)
        super().__init__(
            f"this gateway is configured as part of a cluster (`coordination:`), and {detail}. "
            "A server in one of these modes is a child process of one gateway: peers cannot reach it, "
            "so only the instance holding the management lease can serve it and the others answer as "
            "though it had no tools. Use `remote` mode for servers several replicas must serve, or "
            "remove the `coordination:` block to run this as a single gateway."
        )


#: The modes that run a server as a child process of one gateway. Kept next to
#: the refusal rather than imported from the launcher package: this check runs
#: on configuration, before any launcher exists, and the domain vocabulary for
#: "local" is a value object the config has not been parsed into yet.
_CHILD_PROCESS_MODES: frozenset[str] = frozenset({"subprocess", "docker", "container"})


def refuse_local_modes_in_a_declared_cluster(config: dict[str, Any] | None = None) -> None:
    """Refuse child-process servers where the operator declared replicas.

    Every offender at once, because fixing them one restart at a time is the
    experience this codebase keeps refusing to ship.

    Args:
        config: Full configuration. `coordination` is what makes this a cluster;
            `mcp_servers` is where the modes are.

    Raises:
        LocalModeInDeclaredClusterError: When a declared cluster carries one.
    """
    config = config or {}
    if "coordination" not in config:
        return
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        return
    offenders = [
        (str(server_id), str(spec.get("mode")))
        for server_id, spec in servers.items()
        if isinstance(spec, dict) and str(spec.get("mode", "")).strip().lower() in _CHILD_PROCESS_MODES
    ]
    if offenders:
        raise LocalModeInDeclaredClusterError(offenders)


def refuse_a_cluster_that_cannot_coordinate(config: dict[str, Any] | None = None) -> None:
    """Refuse the two configurations that fail without failing.

    Asked on the axis the operator controls rather than by sniffing the
    environment. A thousand pods each with their own storage are a thousand
    gateways, which is a legitimate thing to run and nobody's business but the
    operator's. A `coordination:` block is the statement that these are meant to
    be *one* gateway with several replicas -- and that requires storage they
    share, which in turn requires that storage was decided at all. Naming a
    backend they cannot share and naming none reach the same place: no lease
    anybody else can see, and every replica managing the fleet.

    **Reads the selected backend, so it must be called after
    `set_persistence_backend`.** Before this refusal grew its second branch,
    calling it too early was merely useless; now it would refuse every
    coordinated deployment, because nothing has been selected yet.

    Args:
        config: Full configuration. A `coordination` block is what makes this a
            cluster.

    Raises:
        ClusterNeedsASelectedBackendError: When the configuration asks for a
            cluster and names no backend to coordinate through.
        ClusterNeedsSharedStorageError: When the configuration asks for a
            cluster and the backend cannot be shared.
    """
    from ...infrastructure.persistence.registry import is_shared
    from .composition import get_persistence_backend

    if "coordination" not in (config or {}):
        return
    backend = get_persistence_backend()
    if backend is None:
        raise ClusterNeedsASelectedBackendError()
    if is_shared(backend):
        return
    raise ClusterNeedsSharedStorageError(type(backend).__name__)


def restore_persisted_fleet(runtime: Any) -> int:
    """Bring back the servers a previous run wrote down.

    **This is the read half of a path whose write half shipped without it.**
    `RecoveryService.recover_mcp_servers` had exactly one caller,
    the since-deleted `bootstrap.runtime.initialize_runtime` (#978), itself
    caller-less -- so the snapshot written on every registration since #794 was
    never read back, and a server registered through the API still did not survive a
    restart. The unit test for #794 called the recovery service directly and
    passed, which is the difference between testing a component and testing that
    it is plugged in.

    Runs after the event store is installed, because each restored aggregate
    replays its own stream to recover the lifecycle state it had -- without
    that, a server that was DEGRADED comes back COLD, which is a circuit breaker
    reset handed out by restarting the process.

    Bootstrap is synchronous and the repositories are not, so this crosses on
    the shared background loop and **waits**: a gateway that begins serving
    before its fleet is restored answers "no such server" for the first few
    seconds after every restart.

    Args:
        runtime: The assembled runtime.

    Returns:
        How many servers were restored.
    """
    from ...infrastructure.async_bridge import BackgroundLoop

    recovery = getattr(runtime, "recovery_service", None)
    persistence = getattr(runtime, "persistence_config", None)
    if recovery is None or persistence is None or not persistence.enabled:
        return 0
    if not persistence.auto_recover:
        logger.info("fleet_restore_disabled", detail="MCP_AUTO_RECOVER is off; persisted servers stay unloaded")
        return 0

    loop = BackgroundLoop()
    try:
        restored = loop.run(recovery.recover_mcp_servers(), RESTORE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 -- fault-barrier: a gateway with no fleet is better than one that will not boot
        # Loud, and not fatal. Refusing to start would turn an unreadable
        # snapshot into an outage for the servers declared in config.yaml, which
        # are already loaded and working by this point.
        logger.error("fleet_restore_failed", error=str(e))
        return 0
    finally:
        loop.close()

    if restored:
        logger.info("fleet_restored", count=len(restored), mcp_server_ids=list(restored))
    return len(restored)
