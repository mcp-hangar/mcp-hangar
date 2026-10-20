"""Domain contracts for persistence layer.

These protocols define the interfaces that infrastructure must implement,
following the Dependency Inversion Principle (DIP) from SOLID.

The domain layer owns these contracts - infrastructure depends on domain,
not the other way around.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from ..value_objects.peer_identity import PeerIdentity
from ..value_objects.provenance import Provenance


class AuditAction(Enum):
    """Types of auditable actions on entities."""

    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    STATE_CHANGED = "state_changed"
    STARTED = "started"
    STOPPED = "stopped"
    DEGRADED = "degraded"
    RECOVERED = "recovered"
    TOOL_INVOKED = "tool_invoked"


@dataclass(frozen=True)
class AuditEntry:
    """Immutable record of an auditable action.

    Value object representing a single audit log entry.
    Immutability ensures audit trail integrity.

    Identity fields capture the caller who triggered the action,
    enabling identity-aware audit queries (e.g. "who invoked tool X?").
    """

    entity_id: str
    entity_type: str
    action: AuditAction
    timestamp: datetime
    actor: str  # who performed the action (system, user, etc.)
    old_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    caller_user_id: str | None = None
    caller_agent_id: str | None = None
    caller_session_id: str | None = None
    caller_principal_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.entity_type,
            "action": self.action.value,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "metadata": self.metadata,
            "correlation_id": self.correlation_id,
            "caller_user_id": self.caller_user_id,
            "caller_agent_id": self.caller_agent_id,
            "caller_session_id": self.caller_session_id,
            "caller_principal_type": self.caller_principal_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditEntry":
        """Deserialize from dictionary."""
        return cls(
            entity_id=data["entity_id"],
            entity_type=data["entity_type"],
            action=AuditAction(data["action"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            actor=data["actor"],
            old_state=data.get("old_state"),
            new_state=data.get("new_state"),
            metadata=data.get("metadata", {}),
            correlation_id=data.get("correlation_id"),
            caller_user_id=data.get("caller_user_id"),
            caller_agent_id=data.get("caller_agent_id"),
            caller_session_id=data.get("caller_session_id"),
            caller_principal_type=data.get("caller_principal_type"),
        )


#: The stored `mode` of a key-pinned server's row (ADR-030 §4e). Not a member of
#: `McpServerMode`: the declared mode stays `remote`, and only the row says this.
PEER_KEY_ROW_MODE = "remote-peer-key"


@dataclass(frozen=True)
class McpServerConfigSnapshot:
    """Immutable snapshot of mcp_server configuration.

    Captures the complete configuration state at a point in time,
    used for persistence and recovery.
    """

    mcp_server_id: str
    mode: str
    command: list[str] | None = None
    image: str | None = None
    endpoint: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    idle_ttl_s: int = 300
    health_check_interval_s: int = 60
    max_consecutive_failures: int = 3
    description: str | None = None
    volumes: list[str] = field(default_factory=list)
    build: dict[str, str] | None = None
    resources: dict[str, str] = field(default_factory=dict)
    network: str = "none"
    read_only: bool = True
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    enabled: bool = True
    # SSRF provenance policy for a remote endpoint, persisted so it survives a
    # restart. Without it, a DISCOVERY server rebuilt from its snapshot would
    # lose its runtime-scoped addresses and the connect-time SSRF guard would
    # treat its legitimate private container IP as HUMAN and refuse it. HUMAN +
    # None is the safe default for an older snapshot that predates these fields.
    #
    # `__post_init__` normalises both, because this dataclass is rebuilt via
    # `McpServerConfigSnapshot(**other.to_dict())` (config_repository) where
    # `to_dict` has already reduced the enum to its string value and the
    # addresses to a list -- so the constructor must accept those forms too.
    provenance: Provenance = Provenance.HUMAN
    runtime_addresses: frozenset[str] | None = None
    # Whether the connect-time SSRF guard applies (see McpServer). Persisted so a
    # server rebuilt from its snapshot keeps the same enforcement it registered
    # with; defaults False for an older snapshot that predates the field.
    enforce_ssrf: bool = False
    # The compiled L7 egress policy in wire form (L7Policy.to_wire()), or None.
    # Persisted so enforcement survives a restart and so peer replicas can read
    # it back -- before this field, the policy lived only in the RAM of the one
    # replica that handled the POST, and every other replica ran denied tools
    # (#991). Wire form rather than the dataclass: it is the one shape the
    # operator, the REST route, and L7Policy.from_dict already agree on.
    l7_policy: dict[str, Any] | None = None
    # The server's `tls` block (it holds no secret), so a server declared with a
    # peer identity comes back with it after a restart and on a peer replica,
    # instead of as a plain server whose key nothing checks (ADR-030 §10).
    tls: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        # Frozen dataclass: assign through object.__setattr__. Accept a
        # Provenance, its string value, or None (-> HUMAN); accept runtime
        # addresses as a frozenset, any iterable of strings, or None.
        prov = self.provenance
        if not isinstance(prov, Provenance):
            prov = Provenance(prov) if prov else Provenance.HUMAN
        object.__setattr__(self, "provenance", prov)

        addrs = self.runtime_addresses
        if addrs is not None and not isinstance(addrs, frozenset):
            addrs = frozenset(str(a) for a in addrs)
        object.__setattr__(self, "runtime_addresses", addrs)

        # A key-pinned server's row is stored under a mode the previous release
        # does not know, so that release declines the row instead of serving
        # the server as a plain one (ADR-030 §4e). It is read back as `remote`,
        # and only with the peer identity that gave it the encoding.
        if self.mode == PEER_KEY_ROW_MODE:
            if PeerIdentity.from_tls(self.tls) is None:
                raise ValueError(
                    f"mcp_server '{self.mcp_server_id}': a {PEER_KEY_ROW_MODE} row without tls.peer_identity"
                )
            object.__setattr__(self, "mode", "remote")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for storage."""
        return {
            "mcp_server_id": self.mcp_server_id,
            "mode": PEER_KEY_ROW_MODE if self.mode == "remote" and PeerIdentity.from_tls(self.tls) else self.mode,
            "command": self.command,
            "image": self.image,
            "endpoint": self.endpoint,
            "env": self.env,
            "idle_ttl_s": self.idle_ttl_s,
            "health_check_interval_s": self.health_check_interval_s,
            "max_consecutive_failures": self.max_consecutive_failures,
            "description": self.description,
            "volumes": self.volumes,
            "build": self.build,
            "resources": self.resources,
            "network": self.network,
            "read_only": self.read_only,
            "user": self.user,
            "tools": self.tools,
            "enabled": self.enabled,
            # JSON-safe forms; __post_init__ reverses them on the way back in.
            "provenance": self.provenance.value,
            "runtime_addresses": (sorted(self.runtime_addresses) if self.runtime_addresses is not None else None),
            "enforce_ssrf": self.enforce_ssrf,
            "l7_policy": self.l7_policy,
            "tls": self.tls,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "McpServerConfigSnapshot":
        """Deserialize from dictionary."""
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")

        return cls(
            mcp_server_id=data["mcp_server_id"],
            mode=data["mode"],
            command=data.get("command"),
            image=data.get("image"),
            endpoint=data.get("endpoint"),
            env=data.get("env", {}),
            idle_ttl_s=data.get("idle_ttl_s", 300),
            health_check_interval_s=data.get("health_check_interval_s", 60),
            max_consecutive_failures=data.get("max_consecutive_failures", 3),
            description=data.get("description"),
            volumes=data.get("volumes", []),
            build=data.get("build"),
            resources=data.get("resources", {}),
            network=data.get("network", "none"),
            read_only=data.get("read_only", True),
            user=data.get("user"),
            tools=data.get("tools"),
            enabled=data.get("enabled", True),
            # __post_init__ normalises the string/list back to enum/frozenset.
            provenance=data.get("provenance", Provenance.HUMAN),
            runtime_addresses=data.get("runtime_addresses"),
            enforce_ssrf=data.get("enforce_ssrf", False),
            l7_policy=data.get("l7_policy"),
            tls=data.get("tls"),
            created_at=datetime.fromisoformat(created_at) if created_at else None,
            updated_at=datetime.fromisoformat(updated_at) if updated_at else None,
        )


class IMcpServerConfigRepository(Protocol):
    """Repository protocol for mcp_server configuration persistence.

    Follows Repository pattern from DDD - mediates between domain
    and data mapping layers using a collection-like interface.
    """

    async def save(self, config: McpServerConfigSnapshot) -> None:
        """Save mcp_server configuration.

        Creates or updates the configuration in persistent storage.

        Args:
            config: McpServer configuration snapshot to save

        Raises:
            PersistenceError: If save operation fails
        """
        ...

    async def save_versioned(self, config: McpServerConfigSnapshot, expected_version: int | None) -> None:
        """Save a configuration only if the row is still the one the caller read (#1954).

        `save` replaces whatever is there, so a replica whose copy is behind
        writes its stale copy back over a peer's newer one -- an older L7 policy
        included -- and the stale write wins. This is the compare-and-set form.

        Args:
            config: The configuration to store.
            expected_version: The version `get_with_version` returned; 0 when
                it returned nothing, meaning no row of any kind may exist; or
                None for a create, which may replace a deleted (disabled) row
                but never a live one.

        The same transaction keeps the peer-key name record (ADR-030 §9): a
        configuration with `tls.peer_identity` claims its name and `key_ref`
        there, and one without may not take a name the record holds. An entry
        outlives the row, so a deleted key-pinned server cannot come back as a
        plain one, and `key_ref` names one server only.

        Raises:
            ConcurrentModificationError: The row changed, was deleted, or (for a
                create) already exists, since the caller read it.
            PeerKeyNameRefused: The record holds the name under another
                `key_ref`, holds the `key_ref` under another name, or holds the
                name and the configuration declares no peer identity.
            PersistenceError: If the write fails for any other reason.
        """
        ...

    async def get_peer_key_names(self) -> dict[str, str]:
        """Every entry of the peer-key name record, name to `key_ref` (ADR-030 §9).

        Raises:
            PersistenceError: If the record cannot be read.
        """
        ...

    async def get(self, mcp_server_id: str) -> McpServerConfigSnapshot | None:
        """Retrieve mcp_server configuration by ID.

        Args:
            mcp_server_id: Unique mcp_server identifier

        Returns:
            Configuration snapshot if found, None otherwise
        """
        ...

    async def get_with_version(self, mcp_server_id: str) -> tuple[McpServerConfigSnapshot, int] | None:
        """Retrieve a live configuration with the version `save_versioned` expects.

        Args:
            mcp_server_id: McpServer identifier

        Returns:
            The configuration and its version, or None when there is no row or
            the server was deleted.
        """
        ...

    async def get_all(self) -> list[McpServerConfigSnapshot]:
        """Retrieve all mcp_server configurations.

        Returns:
            List of all stored configurations
        """
        ...

    async def delete(self, mcp_server_id: str) -> bool:
        """Delete mcp_server configuration.

        Args:
            mcp_server_id: McpServer identifier to delete

        Returns:
            True if deleted, False if not found
        """
        ...

    async def exists(self, mcp_server_id: str) -> bool:
        """Check if mcp_server configuration exists.

        Args:
            mcp_server_id: McpServer identifier to check

        Returns:
            True if exists, False otherwise
        """
        ...


class IAuditRepository(Protocol):
    """Repository protocol for audit log persistence.

    Provides append-only storage for audit entries, ensuring
    immutable audit trail for accountability.
    """

    async def append(self, entry: AuditEntry) -> None:
        """Append an audit entry.

        Audit entries are immutable once written.

        Args:
            entry: Audit entry to append

        Raises:
            PersistenceError: If append operation fails
        """
        ...

    async def get_by_entity(
        self,
        entity_id: str,
        entity_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries for an entity.

        Args:
            entity_id: Entity identifier
            entity_type: Optional entity type filter
            limit: Maximum entries to return
            offset: Number of entries to skip

        Returns:
            List of audit entries, newest first
        """
        ...

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        entity_type: str | None = None,
        action: AuditAction | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        """Get audit entries within a time range.

        Args:
            start: Start of time range (inclusive)
            end: End of time range (inclusive)
            entity_type: Optional entity type filter
            action: Optional action filter
            limit: Maximum entries to return

        Returns:
            List of audit entries, newest first
        """
        ...

    async def get_by_correlation_id(self, correlation_id: str) -> list[AuditEntry]:
        """Get all audit entries for a correlation ID.

        Useful for tracing distributed operations.

        Args:
            correlation_id: Correlation identifier

        Returns:
            List of related audit entries
        """
        ...

    async def get_by_caller(
        self,
        caller_user_id: str,
        action: AuditAction | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """Get audit entries for a specific caller.

        Enables identity-aware audit queries (e.g. "what did user X do?").

        Args:
            caller_user_id: Caller user identifier
            action: Optional action filter
            limit: Maximum entries to return
            offset: Number of entries to skip

        Returns:
            List of audit entries, newest first
        """
        ...


class PersistenceError(Exception):
    """Base exception for persistence operations."""

    pass


class ConfigurationNotFoundError(PersistenceError):
    """Raised when configuration is not found."""

    def __init__(self, mcp_server_id: str):
        self.mcp_server_id = mcp_server_id
        super().__init__(f"Configuration not found for mcp_server: {mcp_server_id}")


class ConcurrentModificationError(PersistenceError):
    """Raised when concurrent modification is detected."""

    def __init__(self, mcp_server_id: str, expected_version: int, actual_version: int):
        self.mcp_server_id = mcp_server_id
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Concurrent modification on mcp_server '{mcp_server_id}': "
            f"expected version {expected_version}, actual {actual_version}"
        )


class PeerKeyNameRefused(PersistenceError):
    """A write would contradict the peer-key name record (ADR-030 §9)."""

    def __init__(self, mcp_server_id: str, reason: str):
        self.mcp_server_id = mcp_server_id
        self.reason = reason
        super().__init__(f"mcp_server '{mcp_server_id}': {reason}")


def peer_key_claim(config: McpServerConfigSnapshot) -> str | None:
    """The `key_ref` a configuration claims in the peer-key name record, if any."""
    identity = PeerIdentity.from_tls(config.tls)
    return identity.key_ref if identity is not None else None


def check_peer_key_claim(mcp_server_id: str, key_ref: str | None, entries: list[tuple[str, str]]) -> None:
    """Refuse a write the record contradicts.

    Args:
        mcp_server_id: The name being written.
        key_ref: The `key_ref` it declares, or None for a server without a peer identity.
        entries: The record's entries whose name is `mcp_server_id` or whose
            `key_ref` is `key_ref`, read after the claim was inserted, so a
            concurrent claim that won is among them.
    """
    if key_ref is None:
        held = [ref for name, ref in entries if name == mcp_server_id]
        if held:
            raise PeerKeyNameRefused(
                mcp_server_id,
                f"the name is in the peer-key name record with key_ref '{held[0]}'; "
                "a server without that peer identity cannot take it",
            )
        return
    for name, ref in entries:
        if name == mcp_server_id and ref != key_ref:
            raise PeerKeyNameRefused(mcp_server_id, f"the name is in the peer-key name record with key_ref '{ref}'")
        if name != mcp_server_id and ref == key_ref:
            raise PeerKeyNameRefused(mcp_server_id, f"key_ref '{key_ref}' already names the server '{name}'")
    if (mcp_server_id, key_ref) not in entries:
        raise PeerKeyNameRefused(mcp_server_id, "the peer-key name record did not keep the claim")


def peer_key_name_conflicts(declared: dict[str, str | None], stored: dict[str, str]) -> list[str]:
    """What a configuration file contradicts in the stored peer-key name record (ADR-030 §9).

    Args:
        declared: Every server the file declares, by name, with the `key_ref`
            of its peer identity or None.
        stored: The record's entries, name to `key_ref`.

    Returns:
        One line per conflict; empty when the file agrees with the record. The
        same name with the same `key_ref` agrees: that is a registered server
        moved into the file. Two names the file gives one `key_ref` conflict
        too, because the file's servers are entries of the record (§7).
    """
    holders = {ref: name for name, ref in stored.items()}
    declared_by: dict[str, str] = {}
    conflicts = []
    for name, key_ref in sorted(declared.items()):
        if key_ref is not None and declared_by.setdefault(key_ref, name) != name:
            conflicts.append(f"'{name}' and '{declared_by[key_ref]}' both declare key_ref '{key_ref}'")
        if name in stored and key_ref != stored[name]:
            conflicts.append(
                f"'{name}' is in the peer-key name record with key_ref '{stored[name]}', "
                f"and the file declares it {'with key_ref ' + repr(key_ref) if key_ref else 'without a peer identity'}"
            )
        elif key_ref is not None and holders.get(key_ref, name) != name:
            conflicts.append(f"'{name}' declares key_ref '{key_ref}', which the record holds for '{holders[key_ref]}'")
    return conflicts


# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderConfigSnapshot")): McpServerConfigSnapshot,
        "".join(("IPro", "viderConfigRepository")): IMcpServerConfigRepository,
    }
)
