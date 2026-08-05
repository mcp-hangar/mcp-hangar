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
