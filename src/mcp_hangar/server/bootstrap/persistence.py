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


def refuse_a_cluster_on_unshared_storage(config: dict[str, Any] | None = None) -> None:
    """Refuse the one combination that fails without failing.

    Asked on the axis the operator controls rather than by sniffing the
    environment. A thousand pods each with their own storage are a thousand
    gateways, which is a legitimate thing to run and nobody's business but the
    operator's. A `coordination:` block is the statement that these are meant to
    be *one* gateway with several replicas -- and that requires storage they
    share.

    Args:
        config: Full configuration. A `coordination` block is what makes this a
            cluster.

    Raises:
        ClusterNeedsSharedStorageError: When the configuration asks for a
            cluster and the backend cannot be shared.
    """
    from ...infrastructure.persistence.registry import is_shared
    from .composition import get_persistence_backend

    if "coordination" not in (config or {}):
        return
    backend = get_persistence_backend()
    if backend is None or is_shared(backend):
        return
    raise ClusterNeedsSharedStorageError(type(backend).__name__)


def restore_persisted_fleet(runtime: Any) -> int:
    """Bring back the servers a previous run wrote down.

    **This is the read half of a path whose write half shipped without it.**
    `RecoveryService.recover_mcp_servers` had exactly one caller,
    `bootstrap.runtime.initialize_runtime`, and *that* function has no callers
    at all -- so the snapshot written on every registration since #794 was never
    read back, and a server registered through the API still did not survive a
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
