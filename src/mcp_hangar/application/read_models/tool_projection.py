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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Literal

from ...domain.contracts.withdrawals import IWithdrawalOverlay
from ...domain.services.digest_computation import compute_tool_digest
from ...domain.value_objects.tool_digest import DigestEnforcement, ToolDigest

if TYPE_CHECKING:
    from ...domain.model.tool_catalog import ToolSchema

logger = logging.getLogger(__name__)

#: Called with no arguments after a mutation that can change what a tenant is
#: projected (#1366). Module-level rather than per instance, so it survives a
#: registry reset. Called outside the registry lock, on the mutating thread, so a
#: listener must hand off rather than do work: the front door's publisher
#: schedules a comparison on its serving loop and returns.
_change_listeners: list[Callable[[], None]] = []


def add_change_listener(listener: Callable[[], None]) -> None:
    """Call *listener* after every projection-changing mutation. Idempotent."""
    if listener not in _change_listeners:
        _change_listeners.append(listener)


def remove_change_listener(listener: Callable[[], None]) -> None:
    """Stop calling *listener*. A listener that was never added is ignored."""
    if listener in _change_listeners:
        _change_listeners.remove(listener)


def _projection_changed() -> None:
    """Tell every listener; one that raises is logged and does not stop the rest."""
    for listener in tuple(_change_listeners):
        try:
            listener()
        except Exception:  # noqa: BLE001 -- fault-barrier: a listener must not fail the mutation that fired it
            logger.warning("tool_projection_change_listener_failed", exc_info=True)


#: Why a listing withholds a tool whose definition drifted from a blocking pin (#1776).
WITHHELD_DIGEST_DRIFT = "digest_drift"

#: Why an operator listing withholds a tool a call of it is refused as withdrawn
#: for every caller: a config or runtime withdrawal for all tenants, or a tool
#: the server's pins do not admit under ``new_tools: withhold`` (#2033).
WITHHELD_WITHDRAWN = "withdrawn"

#: The digest registered for a pin the configuration loader could not read
#: (#2036). No definition hashes to it, so the tool reads as drifted from its pin
#: -- withheld under `block`, reported under `audit` -- and, under
#: `new_tools: withhold`, it does not admit the tool either: a typo must not let
#: through what the operator meant to pin.
UNREADABLE_PIN_SHA256 = "0" * 64

#: The fields of an upstream tool definition a ``block`` pin vouches for: exactly
#: the ones `compute_tool_digest` hashes (SEP-1766). An allow-list, so a field the
#: spec adds later is withheld from a pinned tool until a pin covers it.
PINNED_TOOL_FIELDS = ("name", "description", "inputSchema", "outputSchema")


def pinned_definition(tool: Mapping[str, Any]) -> dict[str, Any]:
    """*tool* cut down to what its pin vouches for: :data:`PINNED_TOOL_FIELDS`.

    A pin digests four fields, so an upstream can change ``title``,
    ``annotations``, ``execution``, ``icons`` or ``_meta`` under a matching
    digest: put instructions in ``title``, or flip ``readOnlyHint`` on to skip a
    client's confirmation. Served without them, a client falls back to the
    spec's defaults, which are the cautious ones. The MCP schema (2025-11-25,
    ``Tool`` and ``ToolAnnotations``,
    https://modelcontextprotocol.io/specification/2025-11-25/schema#toolannotations)
    takes the display name from ``title``, then ``annotations.title``, then
    ``name``, and gives absent hints as ``readOnlyHint: false``,
    ``destructiveHint: true``, ``idempotentHint: false`` and ``openWorldHint:
    true``.

    Applied to the upstream's definition before Hangar adds its own fields
    (the flat name on the front door, ``digest`` and ``pinned_digest`` on
    ``hangar_tools`` and REST), which therefore stay.
    """
    return {key: tool[key] for key in PINNED_TOOL_FIELDS if key in tool}


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


class ToolProjectionRegistry(IWithdrawalOverlay):
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
        # Every server whose catalogue has been projected at least once in this
        # process, an empty catalogue included (#1446). Never shrinks: a stop
        # does not remove a projection, so an idle server still counts.
        self._projected: set[str] = set()
        # Config-withdrawal overlay: (mcp_server, kind, name) -> set of tenant_ids
        # or _ALL_TENANTS sentinel. Populated at config-load time; re-applied on
        # every reload. _ALL_TENANTS means withdrawn for every tenant.
        #
        # `kind` is "tool", "prompt" or "resource" (#1028): a prompt and a
        # resource are withdrawable exactly the way a tool is, and the overlay
        # they share is the same one the reload/runtime split is already correct
        # for. Only tools carry a discovered projection (schema + digest) --
        # prompts and resources are relayed live -- so `_projections` below stays
        # tool-keyed and the kinds meet again at `is_withdrawn`.
        self._config_withdrawals: dict[tuple[str, str, str], set[str] | _AllTenants] = {}
        # Runtime-withdrawal overlay: survives config reloads (clear_config_withdrawals does NOT touch this).
        # Same shape as _config_withdrawals.
        self._runtime_withdrawals: dict[tuple[str, str, str], set[str] | _AllTenants] = {}
        # Config-pin overlay: (mcp_server, tool) -> {tenant_id -> pinned ToolDigest}.
        # Populated at config-load time; re-applied on every reload (#233).
        self._config_pins: dict[tuple[str, str], dict[str, ToolDigest]] = {}
        # All-tenants config-pin overlay: (mcp_server, tool) -> pinned ToolDigest.
        # The counterpart of the `_ALL_TENANTS` withdrawal sentinel, and the only
        # pin an unauthenticated caller can be held to: `resolve_pin` keys the
        # per-tenant map by a tenant id, so with auth off -- where the caller is
        # anonymous and `tenant_id` is always None -- every per-tenant pin missed
        # and the digest capability enforced nothing at all (#902).
        self._config_pins_all_tenants: dict[tuple[str, str], ToolDigest] = {}
        # Per-mcp_server digest-enforcement mode for pin mismatches; an unset
        # server defaults to the strictest (block). Scoped per server so one
        # server's `audit` cannot downgrade another server's pins (#278).
        self._digest_enforcement: dict[str, DigestEnforcement] = {}
        # Servers whose tools that appear after their pins were taken are withheld
        # (`tool_projection.new_tools: withhold`, #1801). Absent: served unpinned.
        self._withhold_new_tools: set[str] = set()

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
            self._projected.add(mcp_server)
            logger.debug(
                "tool_projection_registry_built",
                extra={
                    "mcp_server": mcp_server,
                    "tool_count": len(new_projections),
                },
            )
        _projection_changed()

    def forget_server(self, mcp_server: str) -> bool:
        """Drop *mcp_server*'s projections, because it left the fleet (#2088).

        Only the projections go. Withdrawals, pins, digest enforcement and the
        new-tools policy are keyed by name and stay, so a server added back under
        the same name is withdrawn and pinned as before: clearing them would let
        it back in ungoverned. ``was_projected`` stays true too, as for a stopped
        server, so a required catalogue's readiness is what it was.

        Returns:
            Whether anything was dropped. Listeners are told only then.
        """
        with self._lock:
            stale_keys = [k for k in self._projections if k[0] == mcp_server]
            for k in stale_keys:
                del self._projections[k]
        if not stale_keys:
            return False
        logger.debug(
            "tool_projection_server_forgotten", extra={"mcp_server": mcp_server, "tool_count": len(stale_keys)}
        )
        _projection_changed()
        return True

    # ------------------------------------------------------------------
    # Config-withdrawal overlay (populated at config-load time)
    # ------------------------------------------------------------------

    def set_config_withdrawal(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
        *,
        kind: str = "tool",
    ) -> None:
        """Mark a tool, prompt or resource as withdrawn via config.

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name, prompt name, or upstream resource URI.
            tenant_id: If ``None``, it is withdrawn for ALL tenants.
                Otherwise only for the given tenant.
            kind: What *tool* names -- "tool" (default), "prompt" or "resource".
        """
        key = (mcp_server, kind, tool)
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
        _projection_changed()

    def _is_unadmitted(self, mcp_server: str, tool: str, tenant_id: str | None) -> bool:
        """A tool that appeared after the server's pins were taken, on a server that withholds those (#1801).

        The baseline is the server's own pins: a server with none has no baseline
        and withholds nothing, and a tool pinned for this caller (for its tenant
        or for all tenants) is admitted. Called with the lock held.
        """
        if mcp_server not in self._withhold_new_tools:
            return False
        has_baseline = any(scope == mcp_server for scope, _tool in self._config_pins) or any(
            scope == mcp_server for scope, _tool in self._config_pins_all_tenants
        )
        if not has_baseline:
            return False
        key = (mcp_server, tool)
        everyone = self._config_pins_all_tenants.get(key)
        if everyone is not None and everyone.sha256 != UNREADABLE_PIN_SHA256:
            return False
        if tenant_id is None:
            return True
        theirs = self._config_pins.get(key, {}).get(tenant_id)
        # A pin the loader could not read admits nothing (#2036).
        return theirs is None or theirs.sha256 == UNREADABLE_PIN_SHA256

    def is_unadmitted(self, mcp_server: str, tool: str, tenant_id: str | None = None) -> bool:
        """Is *tool* withheld on *mcp_server* because it is not among the pins (#1801)?"""
        with self._lock:
            return self._is_unadmitted(mcp_server, tool, tenant_id)

    def _is_config_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None, kind: str = "tool") -> bool:
        """Return True if (mcp_server, kind, tool) is config-withdrawn for tenant_id."""
        if kind == "tool" and self._is_unadmitted(mcp_server, tool, tenant_id):
            return True
        entry = self._config_withdrawals.get((mcp_server, kind, tool))
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
        tenant_id: str | None,
        digest: ToolDigest,
    ) -> None:
        """Pin a tool to a specific digest via config.

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name.
            tenant_id: Tenant the pin applies to. ``None`` pins the tool for
                ALL tenants, including callers that carry no tenant identity --
                the same meaning ``None`` has in :meth:`set_config_withdrawal`.
            digest: The :class:`~domain.value_objects.tool_digest.ToolDigest`
                the tool is expected to match.
        """
        with self._lock:
            if tenant_id is None:
                self._config_pins_all_tenants[(mcp_server, tool)] = digest
            else:
                self._config_pins.setdefault((mcp_server, tool), {})[tenant_id] = digest
        logger.debug(
            "config_pin_set",
            extra={"mcp_server": mcp_server, "tool": tool, "tenant_id": tenant_id},
        )

    def set_digest_enforcement(self, mcp_server: str, mode: DigestEnforcement) -> None:
        """Set the digest-enforcement mode applied to *mcp_server*'s pin mismatches."""
        with self._lock:
            self._digest_enforcement[mcp_server] = mode
        logger.debug("digest_enforcement_set", extra={"mcp_server": mcp_server, "mode": mode.value})

    def resolve_pin(self, mcp_server: str, tool: str, tenant_id: str | None) -> ToolDigest | None:
        """Return the pinned digest for *(mcp_server, tool)* and *tenant_id*.

        Resolution is narrowest-first, the same order the tool-access policies
        use: a pin declared for this tenant wins over one declared for all
        tenants. An all-tenants pin applies to a caller with no tenant identity
        -- that is the whole point of it, and it is why this no longer returns
        ``None`` on sight of a ``None`` tenant (#902). A caller with no identity
        still matches no per-tenant pin, which is correct: those pins name a
        tenant this caller has not been shown to be.

        Returns ``None`` only when no pin covers the tool for this caller.
        """
        key = (mcp_server, tool)
        with self._lock:
            if tenant_id is not None:
                tenant_pin = self._config_pins.get(key, {}).get(tenant_id)
                if tenant_pin is not None:
                    return tenant_pin
            return self._config_pins_all_tenants.get(key)

    def config_pins(self) -> list[tuple[str, str, str | None, ToolDigest]]:
        """Every pin as ``(scope id, tool, tenant id or None for all tenants, digest)`` (#1693)."""
        with self._lock:
            found: list[tuple[str, str, str | None, ToolDigest]] = [
                (scope, tool, None, digest) for (scope, tool), digest in self._config_pins_all_tenants.items()
            ]
            for (scope, tool), by_tenant in self._config_pins.items():
                found.extend((scope, tool, tenant_id, digest) for tenant_id, digest in by_tenant.items())
        return found

    def set_new_tools_policy(self, mcp_server: str, policy: str) -> None:
        """Set what happens to a tool that appears after *mcp_server*'s pins: ``withhold`` or ``allow`` (#1801)."""
        if policy not in ("withhold", "allow"):
            raise ValueError(f"new_tools must be 'withhold' or 'allow', got {policy!r}")
        with self._lock:
            if policy == "withhold":
                self._withhold_new_tools.add(mcp_server)
            else:
                self._withhold_new_tools.discard(mcp_server)
        _projection_changed()

    def new_tools_policy(self, mcp_server: str) -> str:
        """``withhold`` or ``allow`` for *mcp_server*."""
        with self._lock:
            return "withhold" if mcp_server in self._withhold_new_tools else "allow"

    def digest_enforcement(self, mcp_server: str) -> DigestEnforcement:
        """Return the digest-enforcement mode for *mcp_server* (block if unset)."""
        with self._lock:
            return self._digest_enforcement.get(mcp_server, DigestEnforcement.BLOCK)

    def clear_config_pins(self) -> None:
        """Remove all config-declared pins and reset enforcement to block.

        Called before re-applying config on reload so that removing a pin (or
        the ``digest_enforcement`` setting) from the config file actually
        reverts to the strict default (#233).
        """
        with self._lock:
            self._config_pins.clear()
            self._config_pins_all_tenants.clear()
            self._digest_enforcement.clear()
            self._withhold_new_tools.clear()
        logger.debug("config_pins_cleared")
        _projection_changed()

    def adopt_config_overlays(self, staged: ToolProjectionRegistry, *, replace: bool) -> None:
        """Take the withdrawals, pins and enforcement modes a configuration registered on *staged*.

        The configuration registers them on a fresh registry, and they are taken
        from it here under this registry's lock. A concurrent resolve sees the
        previous overlays or the new ones, never the empty ones a reload used to
        leave between clearing and re-registering (#1424).

        With *replace*, which is what a reload asks for, the config overlays
        become exactly *staged*'s, so deleting a withdrawal or a pin from the
        file restores the tool and the strict default (#233). Without it they
        are added to what is there, which is what a first load does. Runtime
        withdrawals are never touched either way (#235).
        """
        with staged._lock:
            withdrawals = {
                key: set(entry) if isinstance(entry, set) else entry
                for key, entry in staged._config_withdrawals.items()
            }
            pins = {key: dict(by_tenant) for key, by_tenant in staged._config_pins.items()}
            pins_all_tenants = dict(staged._config_pins_all_tenants)
            enforcement = dict(staged._digest_enforcement)
            withhold = set(staged._withhold_new_tools)
        with self._lock:
            if replace:
                self._withhold_new_tools = withhold
                self._config_withdrawals = withdrawals
                self._config_pins = pins
                self._config_pins_all_tenants = pins_all_tenants
                self._digest_enforcement = enforcement
            else:
                # Added through the setters, so a merge means what registering
                # the same entries one by one has always meant.
                for (mcp_server, kind, name), entry in withdrawals.items():
                    for tenant_id in sorted(entry) if isinstance(entry, set) else [None]:
                        self.set_config_withdrawal(mcp_server, name, tenant_id, kind=kind)
                for (mcp_server, tool), by_tenant in pins.items():
                    for tenant_id, digest in by_tenant.items():
                        self.set_config_pin(mcp_server, tool, tenant_id, digest)
                for (mcp_server, tool), digest in pins_all_tenants.items():
                    self.set_config_pin(mcp_server, tool, None, digest)
                self._digest_enforcement.update(enforcement)
                self._withhold_new_tools |= withhold
        _projection_changed()

    # ------------------------------------------------------------------
    # Runtime-withdrawal overlay (survives config reloads)
    # ------------------------------------------------------------------

    def withdraw(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
        *,
        kind: str = "tool",
    ) -> None:
        """Mark a tool, prompt or resource as withdrawn at runtime (survives config reload).

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name, prompt name, or upstream resource URI.
            tenant_id: If ``None``, it is withdrawn for ALL tenants.
                Otherwise only for the given tenant.
            kind: What *tool* names -- "tool" (default), "prompt" or "resource".
        """
        key = (mcp_server, kind, tool)
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
        _projection_changed()

    def restore(
        self,
        mcp_server: str,
        tool: str,
        tenant_id: str | None = None,
        *,
        kind: str = "tool",
    ) -> None:
        """Remove a runtime withdrawal for a tool, prompt or resource.

        Affects ONLY the runtime overlay; a config-declared withdrawal
        independently persists (effective = config OR runtime).

        Args:
            mcp_server: Owning mcp_server identifier.
            tool: Tool name, prompt name, or upstream resource URI.
            tenant_id: If ``None``, removes the runtime withdrawal for ALL
                tenants (clears the entire key). Otherwise removes the given
                tenant from the per-tenant set.
            kind: What *tool* names -- "tool" (default), "prompt" or "resource".
        """
        key = (mcp_server, kind, tool)
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
        _projection_changed()

    def is_withdrawn_for_all_tenants(self, mcp_server: str, name: str, *, kind: str = "tool") -> bool:
        """Is there a runtime withdrawal of *name* that covers every tenant?

        The one entry a per-tenant :meth:`restore` leaves in place. Config
        withdrawals are not consulted, because restore never touches them either.
        """
        with self._lock:
            return self._runtime_withdrawals.get((mcp_server, kind, name)) is _ALL_TENANTS

    def _is_runtime_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None, kind: str = "tool") -> bool:
        """Return True if (mcp_server, kind, tool) is runtime-withdrawn for tenant_id."""
        entry = self._runtime_withdrawals.get((mcp_server, kind, tool))
        if entry is None:
            return False
        if entry is _ALL_TENANTS:
            return True
        return tenant_id is not None and tenant_id in entry  # type: ignore[operator]

    def _is_withdrawn_for(self, mcp_server: str, tool: str, tenant_id: str | None, kind: str = "tool") -> bool:
        """Return True if config OR runtime withdraws (mcp_server, kind, tool) for tenant_id."""
        return self._is_config_withdrawn_for(mcp_server, tool, tenant_id, kind) or self._is_runtime_withdrawn_for(
            mcp_server, tool, tenant_id, kind
        )

    def withdrawal_status(
        self, mcp_server: str, name: str, tenant_id: str | None, *, kind: str = "tool"
    ) -> bool | None:
        """The overlay as the access decision reads it (:class:`IWithdrawalOverlay`, #1784).

        A tool answers as :meth:`resolve` does, so the decision and the
        executor's withdrawal gate read one status: ``None`` for a tool the
        catalogue does not know, which blocks nothing. A prompt or a resource
        answers as :meth:`is_withdrawn` does.

        The exception is a tool the server's pins do not admit (#2033): it is
        withdrawn whether or not the catalogue holds it yet. Before the cold
        start that builds the catalogue, and for an upstream that answers a
        call of a tool it leaves out of ``tools/list``, ``resolve`` knows no
        such tool, and the first call of it ran.
        """
        if kind != "tool":
            return self.is_withdrawn(mcp_server, name, kind=kind, tenant_id=tenant_id)
        with self._lock:
            projection = self.resolve(mcp_server, name, tenant_id)
            if projection is None:
                return True if self._is_unadmitted(mcp_server, name, tenant_id) else None
            return projection.is_withdrawn_for(tenant_id)

    def is_withdrawn(
        self,
        mcp_server: str,
        name: str,
        *,
        kind: str = "tool",
        tenant_id: str | None = None,
    ) -> bool:
        """Is *name* withdrawn for *tenant_id* by config or at runtime?

        The public form of the overlay, for the surfaces that have no projection
        to read a status off: a prompt and a resource are relayed live, so
        :meth:`resolve` -- which returns a schema and a digest -- has nothing to
        say about them. Tools keep going through :meth:`resolve`, whose answer
        also folds in the base status set at discovery time.
        """
        with self._lock:
            return self._is_withdrawn_for(mcp_server, name, tenant_id, kind)

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
            config_entry = self._config_withdrawals.get((mcp_server, "tool", tool))
            runtime_entry = self._runtime_withdrawals.get((mcp_server, "tool", tool))

            # Is it a blanket (ALL-tenants) withdrawal from either source?
            all_tenants_withdrawn = config_entry is _ALL_TENANTS or runtime_entry is _ALL_TENANTS

            if config_entry is None and runtime_entry is None:
                # Withheld only because it is in none of the server's pins (#1801), for
                # this caller: a tool the catalogue does not know stays unknown, and a
                # known one reads withdrawn for this tenant (or for all, with no tenant).
                if discovered is None:
                    return None
                if tenant_id is None:
                    return replace(discovered, status="withdrawn")
                return replace(discovered, tenant_overrides={**discovered.tenant_overrides, tenant_id: "withdrawn"})

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

    def digest_fields(
        self,
        tool: Mapping[str, Any],
        *,
        named: str,
        served_by: str | None = None,
        tenant_id: str | None = None,
        groups: Iterable[str] = (),
    ) -> dict[str, str]:
        """The digest fields a listing shows next to *tool* (#1528).

        ``digest`` is the SEP-1766 fingerprint of *tool* exactly as listed,
        from :func:`compute_tool_digest` -- the function ``mcp-hangar pin``
        uses. The projection's digest is reused when its schema is the listed
        one, so a listing does not hash again what discovery already hashed.

        ``pinned_digest`` is the pin :meth:`resolve_pin` finds for *tenant_id*
        on the id the caller *named*, then on the server *served_by* (a group's
        selected member) -- the order the call path resolves a call's own pin
        in. It is omitted when there is none, as a tool object omits every unset
        optional field. Pins a member inherits from the groups that own it are
        not shown here.

        ``withheld`` is :data:`WITHHELD_DIGEST_DRIFT` when the tool drifted from
        a blocking pin on *named*, *served_by* or one of *groups* (the groups
        that own the server named), as :meth:`drifted_pin` decides (#1776). A
        listing a model reads drops such a tool; one an operator reads shows
        it with this reason.

        Callers pass only the tools the caller was allowed to list, so a tool
        the caller cannot see gets no digest and no pin.
        """
        name = str(tool.get("name", ""))
        server = served_by or named
        with self._lock:
            projection = self._projections.get((server, name))
        if projection is not None and projection.schema == dict(tool):
            digest = projection.digest.sha256
        else:
            digest = compute_tool_digest(dict(tool)).sha256
        fields = {"digest": digest}
        pin = self.resolve_pin(named, name, tenant_id)
        if pin is None and server != named:
            pin = self.resolve_pin(server, name, tenant_id)
        if pin is not None:
            fields["pinned_digest"] = pin.sha256
        if self.drifted_pin(name, digest, tenant_id, dict.fromkeys((named, server, *groups))) is not None:
            fields["withheld"] = WITHHELD_DIGEST_DRIFT
        return fields

    def drifted_pin(
        self, tool: str, digest: str, tenant_id: str | None, scopes: Iterable[str]
    ) -> tuple[str, ToolDigest] | None:
        """The first ``(scope, pin)`` on *scopes* that *tool* at *digest* drifted from and that blocks (#1776).

        A listing withholds such a tool. The digest gate refuses the call
        already, and listing the tool still put the drifted description -- the
        rug pull's instruction -- in front of the model on every listing. Only
        a pin declared on an id in ``block`` mode counts: under ``warn`` and
        ``audit`` the call goes through, and a listing shows what can be
        called. The SHA-256 is compared and nothing else: a pin bound to
        another upstream (#1802) is the call gate's to refuse, and the
        description such a tool carries is the pinned one.

        Pure, with no event and no log: a front-door projection is generated
        for every channel on every change.

        Args:
            tool: The tool's name.
            digest: The SHA-256 of the definition the listing would show.
            tenant_id: The caller's tenant; each pin resolves as for the call.
            scopes: Each id whose pins hold the tool: the server, and the groups
                that own it or the group the listing was made through.
        """
        with self._lock:
            for scope in scopes:
                pin = self.resolve_pin(scope, tool, tenant_id)
                if pin is None or pin.sha256 == digest:
                    continue
                if self._digest_enforcement.get(scope, DigestEnforcement.BLOCK) == DigestEnforcement.BLOCK:
                    return scope, pin
        return None

    def served_definition(
        self,
        tool: Mapping[str, Any],
        tenant_id: str | None,
        scopes: Iterable[str],
        *,
        any_tenant: bool = False,
    ) -> dict[str, Any]:
        """The definition of *tool* a listing may serve: :func:`pinned_definition` under a ``block`` pin.

        Every surface that serves a tool definition calls this, with the
        scopes its drift check uses. A pin in ``block`` mode on any of
        *scopes*, resolved for *tenant_id* as for the call, cuts the tool down
        to the fields the pin covers, drifted or not. With *any_tenant* (a
        fleet-wide surface such as REST) a pin for any tenant counts. A tool no
        such pin covers, or one under ``warn`` or ``audit``, is served whole.
        """
        name = str(tool.get("name", ""))
        with self._lock:
            for scope in scopes:
                if self._digest_enforcement.get(scope, DigestEnforcement.BLOCK) != DigestEnforcement.BLOCK:
                    continue
                if any_tenant:
                    key = (scope, name)
                    pinned = key in self._config_pins_all_tenants or bool(self._config_pins.get(key))
                else:
                    pinned = self.resolve_pin(scope, name, tenant_id) is not None
                if pinned:
                    return pinned_definition(tool)
        return dict(tool)

    def drifted_for_any_tenant(self, tool: str, digest: str, scopes: Iterable[str]) -> bool:
        """Whether *tool* at *digest* drifted from a blocking pin on *scopes* for any tenant or all (#1776).

        For a fleet-wide surface such as REST, which has no tenant to resolve
        a pin for: a tool withheld from any tenant is shown withheld there too.
        """
        with self._lock:
            for scope in scopes:
                if self._digest_enforcement.get(scope, DigestEnforcement.BLOCK) != DigestEnforcement.BLOCK:
                    continue
                pins = [*self._config_pins.get((scope, tool), {}).values()]
                if (all_tenants := self._config_pins_all_tenants.get((scope, tool))) is not None:
                    pins.append(all_tenants)
                if any(pin.sha256 != digest for pin in pins):
                    return True
        return False

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
            self._config_pins_all_tenants.clear()
            self._digest_enforcement.clear()
            self._built = False
            self._projected.clear()
            logger.debug("tool_projection_registry_invalidated")

    def was_projected(self, mcp_server: str) -> bool:
        """Whether *mcp_server*'s catalogue has been projected at least once on this replica.

        True from its first ``McpServerStarted`` handled here, even when it
        discovered no tools, and it stays true when the server stops: this is
        what a front-door replica's readiness waits for (#1446), and what the
        catalogue retry never restarts a server after. A peer's start does not
        count, since the handler that projects is a local view (#922).
        """
        with self._lock:
            return mcp_server in self._projected

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
