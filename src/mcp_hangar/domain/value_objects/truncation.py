"""Value objects for response truncation.

Contains:
- TruncationConfig - configuration for batch response truncation
- ContinuationId - identifier for retrieving full response content
- ContinuationOwner - the caller a continuation answers to
"""

import uuid
from dataclasses import dataclass
from typing import Any

from .identity import IdentityContext


@dataclass(frozen=True)
class TruncationConfig:
    """Configuration for batch response truncation.

    Controls how large batch responses are truncated to fit within
    Claude's context limits, with caching for full response retrieval.

    Attributes:
        enabled: Whether truncation is enabled (opt-in).
        max_batch_size_bytes: Maximum total batch response size (~900KB).
        min_per_response_bytes: Minimum bytes allocated per response.
        cache_ttl_s: Time-to-live for cached full responses.
        cache_driver: Cache backend ('memory' or 'redis').
        redis_url: Redis connection URL (required if cache_driver is 'redis').
        max_cache_entries: Maximum entries in memory cache.
        preserve_json_structure: Truncate while preserving JSON validity.
        truncate_on_line_boundary: Truncate text at line boundaries.
    """

    enabled: bool = False
    max_batch_size_bytes: int = 900_000  # ~900KB (margin for metadata)
    min_per_response_bytes: int = 10_000  # 10KB minimum each
    cache_ttl_s: int = 300  # 5 minutes
    cache_driver: str = "memory"
    redis_url: str | None = None
    max_cache_entries: int = 10_000
    preserve_json_structure: bool = True
    truncate_on_line_boundary: bool = True

    def __post_init__(self) -> None:
        """Validate configuration values."""
        if self.max_batch_size_bytes <= 0:
            raise ValueError("max_batch_size_bytes must be positive")
        if self.min_per_response_bytes <= 0:
            raise ValueError("min_per_response_bytes must be positive")
        if self.min_per_response_bytes > self.max_batch_size_bytes:
            raise ValueError("min_per_response_bytes cannot exceed max_batch_size_bytes")
        if self.cache_ttl_s <= 0:
            raise ValueError("cache_ttl_s must be positive")
        if self.cache_driver not in ("memory", "redis"):
            raise ValueError("cache_driver must be 'memory' or 'redis'")
        if self.cache_driver == "redis" and not self.redis_url:
            raise ValueError("redis_url is required when cache_driver is 'redis'")
        if self.max_cache_entries <= 0:
            raise ValueError("max_cache_entries must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TruncationConfig":
        """Create TruncationConfig from a dictionary.

        Args:
            data: Configuration dictionary. If None, returns default config.

        Returns:
            TruncationConfig instance.

        Raises:
            ValueError: If configuration values are invalid.
        """
        if not data:
            return cls()

        return cls(
            enabled=data.get("enabled", False),
            max_batch_size_bytes=data.get("max_batch_size_bytes", 900_000),
            min_per_response_bytes=data.get("min_per_response_bytes", 10_000),
            cache_ttl_s=data.get("cache_ttl_s", 300),
            cache_driver=data.get("cache_driver", "memory"),
            redis_url=data.get("redis_url"),
            max_cache_entries=data.get("max_cache_entries", 10_000),
            preserve_json_structure=data.get("preserve_json_structure", True),
            truncate_on_line_boundary=data.get("truncate_on_line_boundary", True),
        )


@dataclass(frozen=True)
class ContinuationId:
    """Identifier for retrieving full response from a truncated result.

    Format: cont_{batch_id}_{call_index}_{uuid8}

    Attributes:
        value: The continuation ID string.
    """

    value: str

    def __post_init__(self) -> None:
        """Validate continuation ID format."""
        if not self.value:
            raise ValueError("ContinuationId cannot be empty")
        if not self.value.startswith("cont_"):
            raise ValueError("ContinuationId must start with 'cont_'")

    @classmethod
    def generate(cls, batch_id: str, call_index: int) -> "ContinuationId":
        """Generate a new continuation ID.

        Args:
            batch_id: The batch ID this continuation belongs to.
            call_index: The index of the call within the batch.

        Returns:
            A new ContinuationId instance.
        """
        uuid8 = uuid.uuid4().hex[:8]
        return cls(value=f"cont_{batch_id}_{call_index}_{uuid8}")

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        # The random suffix is what keeps an id unguessable, so a repr that
        # reaches a log or a traceback must not carry it.
        return f"ContinuationId('{continuation_log_ref(self.value)}')"


def continuation_log_ref(continuation_id: str) -> str:
    """Name a continuation in a log line without its random suffix.

    A generated id is ``cont_{batch_id}_{call_index}_{uuid8}``. This returns
    ``cont_{batch_id}_{call_index}``, which is what correlation needs: the batch
    id is logged by ``batch_completed`` already. The suffix is dropped rather
    than hashed. With the batch id and call index known, a hash of 32 random
    bits can be reversed by trying them all.

    A caller-supplied string that does not have that shape is cut the same way,
    at its last underscore.
    """
    head, sep, _suffix = continuation_id.rpartition("_")
    return head if sep and head else "cont_?"


@dataclass(frozen=True)
class ContinuationOwner:
    """Who may read back a truncated result: the caller whose call produced it.

    A continuation used to be cached under its id alone, so
    anyone who held the id could fetch or delete it. The cache now stores this
    owner with the payload and answers any other caller exactly as it answers
    an id it does not hold.

    ``tenant_id`` and ``principal_id`` come from the caller's identity context.
    Both ``None`` is the anonymous owner: a call made with no identity, which is
    auth off, or stdio with no declared principal. An anonymous entry is
    fetchable by an anonymous caller, as before, and by no authenticated one.

    The match is exact on both fields. Neither a caller with no tenant nor
    another principal in the same tenant is the owner.
    """

    tenant_id: str | None = None
    principal_id: str | None = None

    @classmethod
    def of(cls, identity: IdentityContext | None) -> "ContinuationOwner":
        """The owner a call made under *identity* records, and the caller it is compared with."""
        caller = identity.caller if identity is not None else None
        if caller is None:
            return cls()
        # The same principal key the task-ownership registry records (TaskOwner).
        return cls(tenant_id=caller.tenant_id or None, principal_id=caller.user_id or caller.agent_id or None)

    def admits(self, caller: "ContinuationOwner") -> bool:
        """Whether *caller* may read or delete what this owner stored."""
        return self == caller

    def to_dict(self) -> dict[str, str | None]:
        """The stored form, for a backend that serializes the owner (Redis)."""
        return {"tenant_id": self.tenant_id, "principal_id": self.principal_id}

    @classmethod
    def from_dict(cls, data: Any) -> "ContinuationOwner":
        """Read back what :meth:`to_dict` wrote.

        Raises:
            ValueError: If *data* is not that shape. A backend answers such an
                entry as not found rather than guess whose it is.
        """
        if not isinstance(data, dict) or set(data) != {"tenant_id", "principal_id"}:
            raise ValueError("continuation owner must be an object with tenant_id and principal_id")
        tenant_id, principal_id = data["tenant_id"], data["principal_id"]
        if not all(value is None or isinstance(value, str) for value in (tenant_id, principal_id)):
            raise ValueError("continuation owner fields must be strings or null")
        return cls(tenant_id=tenant_id or None, principal_id=principal_id or None)
