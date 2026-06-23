"""ToolProjection read-model and ToolProjectionRegistry.

Provides a central, tenant-aware catalog of all backend tools across all
mcp_servers.  The registry is built from the discovery layer (tool schemas
already stored in the domain model) and cached; the cache is invalidated on
config reload so that status changes (active / withdrawn) propagate without a
process restart.

Thread-safe: uses RLock throughout.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from ...domain.services.digest_computation import compute_tool_digest
from ...domain.value_objects.tool_digest import ToolDigest

if TYPE_CHECKING:
    from ...domain.model.tool_catalog import ToolSchema

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Read-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolProjection:
    """Immutable read-model for a single backend tool.

    Attributes:
        mcp_server: Owning mcp_server identifier.
        tool: Tool name as reported by the backend.
        schema: Full JSON-Schema dict (name, description, inputSchema, …).
        digest: SEP-1766 SHA-256 fingerprint of the canonical schema.
        status: Base status – "active" (default) or "withdrawn".
        tenant_overrides: Per-tenant status overrides keyed by tenant_id.
    """

    mcp_server: str
    tool: str
    schema: dict
    digest: ToolDigest
    status: Literal["active", "withdrawn"] = "active"
    tenant_overrides: Mapping[str, str] = field(default_factory=dict)

    def effective_status(self, tenant_id: str | None = None) -> str:
        """Return the status that applies for *tenant_id*.

        If *tenant_id* is ``None`` or has no override, the base ``status``
        is returned.
        """
        if tenant_id is not None and tenant_id in self.tenant_overrides:
            return self.tenant_overrides[tenant_id]
        return self.status

    def is_withdrawn_for(self, tenant_id: str | None = None) -> bool:
        """Return ``True`` when this tool is withdrawn for *tenant_id*."""
        return self.effective_status(tenant_id) == "withdrawn"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolProjectionRegistry:
    """Central, cached, tenant-aware catalog of all backend tools.

    Built from the domain repository (tool schemas already discovered);
    cached for the lifetime of a config epoch; invalidated on config reload
    via :meth:`invalidate`.

    Thread-safe: uses RLock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Primary store: (mcp_server, tool) -> ToolProjection
        self._projections: dict[tuple[str, str], ToolProjection] = {}
        # Tracks whether the registry has been populated at least once
        self._built: bool = False

    # ------------------------------------------------------------------
    # Population (called by bootstrap / config-reload)
    # ------------------------------------------------------------------

    def build_from_tools(
        self,
        mcp_server: str,
        tools: list[ToolSchema],
        *,
        status_overrides: Mapping[str, Literal["active", "withdrawn"]] | None = None,
        tenant_overrides: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        """Populate projections for *mcp_server* from its discovered tools.

        Existing projections for *mcp_server* are replaced atomically.

        Args:
            mcp_server: Owning mcp_server identifier.
            tools: Discovered :class:`~domain.model.tool_catalog.ToolSchema` list.
            status_overrides: Optional per-tool base-status overrides,
                keyed by tool name.  Values must be "active" or "withdrawn".
            tenant_overrides: Optional per-tool tenant overrides, keyed by
                tool name then by tenant_id.
        """
        status_overrides = status_overrides or {}
        tenant_overrides = tenant_overrides or {}

        new_projections: dict[tuple[str, str], ToolProjection] = {}
        for tool_schema in tools:
            tool_dict = tool_schema.to_dict()
            digest = compute_tool_digest(tool_dict)
            base_status: Literal["active", "withdrawn"] = status_overrides.get(
                tool_schema.name, "active"
            )
            per_tenant = dict(tenant_overrides.get(tool_schema.name, {}))
            projection = ToolProjection(
                mcp_server=mcp_server,
                tool=tool_schema.name,
                schema=tool_dict,
                digest=digest,
                status=base_status,
                tenant_overrides=per_tenant,
            )
            new_projections[(mcp_server, tool_schema.name)] = projection

        with self._lock:
            # Remove stale entries for this server, add new ones
            stale_keys = [k for k in self._projections if k[0] == mcp_server]
            for k in stale_keys:
                del self._projections[k]
            self._projections.update(new_projections)
            self._built = True
            logger.debug(
                "tool_projection_registry_built",
                extra={
                    "mcp_server": mcp_server,
                    "tool_count": len(new_projections),
                },
            )

    # ------------------------------------------------------------------
    # Query API (read-only)
    # ------------------------------------------------------------------

    def resolve(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
    ) -> ToolProjection | None:
        """Return the :class:`ToolProjection` for *(mcp_server, tool)*.

        Returns ``None`` when the tool is unknown.  The *tenant_id* argument
        is available for callers that want to inspect effective status
        immediately; the returned projection carries ``tenant_overrides`` so
        callers can also call :meth:`ToolProjection.is_withdrawn_for`.

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: Optional tenant identifier (informational — the full
                projection is returned regardless so callers can re-check for
                other tenants without a second lookup).

        Returns:
            The matching :class:`ToolProjection`, or ``None``.
        """
        with self._lock:
            return self._projections.get((mcp_server, tool))

    def list_for_server(self, mcp_server: str) -> list[ToolProjection]:
        """Return all projections for *mcp_server* (snapshot)."""
        with self._lock:
            return [p for (s, _), p in self._projections.items() if s == mcp_server]

    def all(self) -> list[ToolProjection]:
        """Return a snapshot of all projections across all servers."""
        with self._lock:
            return list(self._projections.values())

    # ------------------------------------------------------------------
    # Cache invalidation
    # ------------------------------------------------------------------

    def invalidate(self) -> None:
        """Discard all cached projections.

        Called on config reload so the registry is rebuilt on the next
        :meth:`build_from_tools` call.
        """
        with self._lock:
            self._projections.clear()
            self._built = False
            logger.debug("tool_projection_registry_invalidated")

    @property
    def is_built(self) -> bool:
        """``True`` if the registry has been populated at least once."""
        with self._lock:
            return self._built


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_registry: ToolProjectionRegistry | None = None
_registry_lock = threading.Lock()


def get_tool_projection_registry() -> ToolProjectionRegistry:
    """Return the process-global :class:`ToolProjectionRegistry` singleton."""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = ToolProjectionRegistry()
    return _registry


def reset_tool_projection_registry() -> None:
    """Reset the singleton (useful for testing)."""
    global _registry
    with _registry_lock:
        if _registry is not None:
            _registry.invalidate()
        _registry = None
