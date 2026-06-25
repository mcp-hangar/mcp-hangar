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
from ...domain.value_objects.tool_digest import DigestEnforcement, ToolDigest

if TYPE_CHECKING:
    from ...domain.model.tool_catalog import ToolSchema

logger = logging.getLogger(__name__)


class _AllTenants:
    """Sentinel type: a withdrawal entry that applies to ALL tenants."""


# Sentinel value: marks a withdrawal that applies to ALL tenants.
# An overlay entry is therefore `set[str] | _AllTenants`; narrow with
# `isinstance(entry, set)` to operate on the per-tenant set.
_ALL_TENANTS = _AllTenants()


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
        # Config-withdrawal overlay: (mcp_server, tool) -> set of tenant_ids or _ALL_TENANTS sentinel.
        # Populated at config-load time; re-applied on every reload.
        # _ALL_TENANTS sentinel means withdrawn for every tenant (no per-tenant check needed).
        self._config_withdrawals: dict[tuple[str, str], set[str] | _AllTenants] = {}
        # Runtime-withdrawal overlay: survives config reloads (clear_config_withdrawals does NOT touch this).
        # Same shape as _config_withdrawals: (mcp_server, tool) -> set[str] | _ALL_TENANTS.
        self._runtime_withdrawals: dict[tuple[str, str], set[str] | _AllTenants] = {}
        # Config-pin overlay: (mcp_server, tool) -> {tenant_id -> pinned ToolDigest}.
        # Populated at config-load time; re-applied on every reload (#233).
        self._config_pins: dict[tuple[str, str], dict[str, ToolDigest]] = {}
        # Digest-enforcement mode for pin mismatches; defaults to the strictest (block).
        self._digest_enforcement: DigestEnforcement = DigestEnforcement.BLOCK

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
            base_status: Literal["active", "withdrawn"] = status_overrides.get(tool_schema.name, "active")
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
    # Config-withdrawal overlay (populated at config-load time)
    # ------------------------------------------------------------------

    def set_config_withdrawal(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
    ) -> None:
        """Mark a tool as withdrawn via config.

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: If ``None``, the tool is withdrawn for ALL tenants.
                Otherwise only for the given tenant.
        """
        key = (mcp_server, tool)
        with self._lock:
            current = self._config_withdrawals.get(key)
            if tenant_id is None:
                # ALL-tenants sentinel — overrides any per-tenant set.
                self._config_withdrawals[key] = _ALL_TENANTS
            elif isinstance(current, set):
                current.add(tenant_id)
            elif current is None:
                self._config_withdrawals[key] = {tenant_id}
            # else: current is _ALL_TENANTS — already the broader rule, keep it.
        logger.debug(
            "config_withdrawal_set",
            extra={"mcp_server": mcp_server, "tool": tool, "tenant_id": tenant_id},
        )

    def clear_config_withdrawals(self) -> None:
        """Remove all config-declared withdrawals.

        Called before re-applying config on reload so that removing a
        withdrawal from the config file actually restores the tool.

        IMPORTANT: Does NOT touch ``_runtime_withdrawals`` — runtime
        withdrawals are intentionally reload-safe (issue #235).
        """
        with self._lock:
            self._config_withdrawals.clear()
        logger.debug("config_withdrawals_cleared")

    def _is_config_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None) -> bool:
        """Return True if (mcp_server, tool) is config-withdrawn for tenant_id."""
        entry = self._config_withdrawals.get((mcp_server, tool))
        if entry is None:
            return False
        if entry is _ALL_TENANTS:
            return True
        # entry is a set[str]
        return tenant_id is not None and tenant_id in entry  # type: ignore[operator]

    # ------------------------------------------------------------------
    # Config-pin overlay (populated at config-load time)
    # ------------------------------------------------------------------

    def set_config_pin(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str,
        digest: ToolDigest,
    ) -> None:
        """Pin a tool to a specific digest for a tenant via config.

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: Tenant the pin applies to.
            digest: The :class:`~domain.value_objects.tool_digest.ToolDigest`
                the tool is expected to match for this tenant.
        """
        with self._lock:
            self._config_pins.setdefault((mcp_server, tool), {})[tenant_id] = digest
        logger.debug(
            "config_pin_set",
            extra={"mcp_server": mcp_server, "tool": tool, "tenant_id": tenant_id},
        )

    def set_digest_enforcement(self, mode: DigestEnforcement) -> None:
        """Set the digest-enforcement mode applied to pin mismatches."""
        with self._lock:
            self._digest_enforcement = mode
        logger.debug("digest_enforcement_set", extra={"mode": mode.value})

    def resolve_pin(self, mcp_server: str, tool: str, tenant_id: str | None) -> ToolDigest | None:
        """Return the pinned digest for *(mcp_server, tool)* and *tenant_id*.

        Returns ``None`` when *tenant_id* is ``None`` or no pin is registered.
        """
        if tenant_id is None:
            return None
        with self._lock:
            return self._config_pins.get((mcp_server, tool), {}).get(tenant_id)

    def digest_enforcement(self) -> DigestEnforcement:
        """Return the current digest-enforcement mode."""
        with self._lock:
            return self._digest_enforcement

    def clear_config_pins(self) -> None:
        """Remove all config-declared pins and reset enforcement to block.

        Called before re-applying config on reload so that removing a pin (or
        the ``digest_enforcement`` setting) from the config file actually
        reverts to the strict default (#233).
        """
        with self._lock:
            self._config_pins.clear()
            self._digest_enforcement = DigestEnforcement.BLOCK
        logger.debug("config_pins_cleared")

    # ------------------------------------------------------------------
    # Runtime-withdrawal overlay (survives config reloads)
    # ------------------------------------------------------------------

    def withdraw(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
    ) -> None:
        """Mark a tool as withdrawn at runtime (survives config reload).

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: If ``None``, the tool is withdrawn for ALL tenants.
                Otherwise only for the given tenant.
        """
        key = (mcp_server, tool)
        with self._lock:
            current = self._runtime_withdrawals.get(key)
            if tenant_id is None:
                self._runtime_withdrawals[key] = _ALL_TENANTS
            elif isinstance(current, set):
                current.add(tenant_id)
            elif current is None:
                self._runtime_withdrawals[key] = {tenant_id}
            # else: current is _ALL_TENANTS — already covers all tenants, keep it.
        logger.debug(
            "runtime_withdrawal_set",
            extra={"mcp_server": mcp_server, "tool": tool, "tenant_id": tenant_id},
        )

    def restore(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
    ) -> None:
        """Remove a runtime withdrawal for a tool.

        Affects ONLY the runtime overlay; a config-declared withdrawal
        independently persists (effective = config OR runtime).

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: If ``None``, removes the runtime withdrawal for ALL
                tenants (clears the entire key). Otherwise removes the given
                tenant from the per-tenant set.
        """
        key = (mcp_server, tool)
        with self._lock:
            current = self._runtime_withdrawals.get(key)
            if current is None:
                return  # Nothing to restore.
            if tenant_id is None:
                # Remove the entire entry → no runtime withdrawal remains.
                del self._runtime_withdrawals[key]
            elif isinstance(current, set):
                current.discard(tenant_id)
                if not current:
                    del self._runtime_withdrawals[key]
            # else: current is _ALL_TENANTS — can't partially remove from an
            # ALL-tenants entry; do nothing to avoid re-enabling other tenants.
        logger.debug(
            "runtime_withdrawal_restored",
            extra={"mcp_server": mcp_server, "tool": tool, "tenant_id": tenant_id},
        )

    def _is_runtime_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None) -> bool:
        """Return True if (mcp_server, tool) is runtime-withdrawn for tenant_id."""
        entry = self._runtime_withdrawals.get((mcp_server, tool))
        if entry is None:
            return False
        if entry is _ALL_TENANTS:
            return True
        return tenant_id is not None and tenant_id in entry  # type: ignore[operator]

    def _is_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None) -> bool:
        """Return True if config OR runtime says (mcp_server, tool) is withdrawn for tenant_id."""
        return self._is_config_withdrawn_for(mcp_server, tool, tenant_id) or self._is_runtime_withdrawn_for(
            mcp_server, tool, tenant_id
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

        Consults the config-withdrawal overlay first.  If the tool is
        config-withdrawn for *tenant_id* (or for ALL tenants), a projection
        marked ``withdrawn`` is returned even when the tool has not yet been
        discovered (no ``build_from_tools`` call).  A placeholder digest with
        all-zero hex is used for undiscovered tools — it is valid per the
        :class:`~domain.value_objects.tool_digest.ToolDigest` schema (64 hex
        chars) and carries no semantic meaning.

        Returns ``None`` only when the tool is completely unknown (not in the
        discovered store AND not config-withdrawn for this tenant).

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
            discovered = self._projections.get((mcp_server, tool))
            withdrawn = self._is_withdrawn_for(mcp_server, tool, tenant_id)

            if not withdrawn:
                # Neither config nor runtime overlay applies — return discovered as-is.
                return discovered

            # At least one overlay applies: build a withdrawn projection.
            # Collect ALL tenants withdrawn by either overlay for per-tenant synthesis.
            config_entry = self._config_withdrawals.get((mcp_server, tool))
            runtime_entry = self._runtime_withdrawals.get((mcp_server, tool))

            # Is it a blanket (ALL-tenants) withdrawal from either source?
            all_tenants_withdrawn = config_entry is _ALL_TENANTS or runtime_entry is _ALL_TENANTS

            if discovered is not None:
                # Discovered projection exists — augment it so is_withdrawn_for() fires.
                if all_tenants_withdrawn:
                    return ToolProjection(
                        mcp_server=discovered.mcp_server,
                        tool=discovered.tool,
                        schema=discovered.schema,
                        digest=discovered.digest,
                        status="withdrawn",
                        tenant_overrides=discovered.tenant_overrides,
                    )
                else:
                    # Merge per-tenant sets from both overlays.
                    merged_overrides = dict(discovered.tenant_overrides)
                    for entry in (config_entry, runtime_entry):
                        if isinstance(entry, set):
                            for tid in entry:
                                merged_overrides[tid] = "withdrawn"
                    return ToolProjection(
                        mcp_server=discovered.mcp_server,
                        tool=discovered.tool,
                        schema=discovered.schema,
                        digest=discovered.digest,
                        status=discovered.status,
                        tenant_overrides=merged_overrides,
                    )

            # Tool not yet discovered — synthesize a minimal withdrawn projection.
            # Placeholder digest: 64 zeros (valid hex, no semantic meaning).
            placeholder_digest = ToolDigest(tool_name=tool, sha256="0" * 64)
            if all_tenants_withdrawn:
                return ToolProjection(
                    mcp_server=mcp_server,
                    tool=tool,
                    schema={},
                    digest=placeholder_digest,
                    status="withdrawn",
                )
            else:
                # Collect union of per-tenant entries from both overlays.
                merged_tenants: set[str] = set()
                for entry in (config_entry, runtime_entry):
                    if entry is not None and entry is not _ALL_TENANTS:
                        merged_tenants.update(entry)  # type: ignore[arg-type]
                overrides = dict.fromkeys(merged_tenants, "withdrawn")
                return ToolProjection(
                    mcp_server=mcp_server,
                    tool=tool,
                    schema={},
                    digest=placeholder_digest,
                    status="active",
                    tenant_overrides=overrides,
                )

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
        """Discard all cached projections, overlays, and pin state.

        Full reset — intended for testing only.  In production, config reload
        calls :meth:`clear_config_withdrawals` (which preserves runtime
        withdrawals) and :meth:`clear_config_pins` rather than this method.
        """
        with self._lock:
            self._projections.clear()
            self._config_withdrawals.clear()
            self._runtime_withdrawals.clear()
            self._config_pins.clear()
            self._digest_enforcement = DigestEnforcement.BLOCK
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
