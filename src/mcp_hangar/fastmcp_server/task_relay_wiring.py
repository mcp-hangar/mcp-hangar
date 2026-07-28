"""Shared ADR-014 governed task-relay wiring.

The relay serving surface (``GovernedTaskStore`` + ``tasks/*`` handlers +
``ctx.governed_task_store``) must activate identically no matter how the MCP
server is built. It used to live only in ``MCPServerFactory``, so the HTTP-serve
bootstrap -- which builds ``FastMCP`` directly (``server/bootstrap``) -- never
wired it: ``ctx.governed_task_store`` stayed ``None`` and every upstream task
handle was rejected with ``TaskRelayNotSupported`` regardless of
``relay_tasks_enabled``. Both the factory path and the bootstrap path now call
these functions, so activation is a pure function of the kill-switch.
"""

from __future__ import annotations

from typing import Any

from .._sdk_compat import FastMCP, lowlevel_server
from ..logging_config import get_logger

logger = get_logger(__name__)


def enable_governed_task_relay(mcp: FastMCP, *, relay_tasks_enabled: bool) -> None:
    """Wire the ADR-014 governed task-relay serving surface (Phase 2).

    Registers the four ``tasks/*`` serving handlers and publishes the shared
    ``GovernedTaskStore`` / consent gate / upstream router onto the
    ApplicationContext ONLY when the v2-native Tasks SDK is present AND
    ``relay_tasks_enabled`` is True. Off (either gate false) nothing is
    registered -- byte-identical to the relay-only stance (ADR-008): no
    ``tasks/*`` handlers, upstream task handles rejected.
    """
    from .._sdk_compat import HAS_NATIVE_TASKS

    if not (HAS_NATIVE_TASKS and relay_tasks_enabled):
        # Dark: relay-only stance preserved (ADR-008). Nothing registered.
        logger.info(
            "governed_tasks_disabled",
            relay_tasks_enabled=relay_tasks_enabled,
            native_tasks=HAS_NATIVE_TASKS,
        )
        return

    from ..application.tasks import GovernedTaskStore
    from ..domain.services.task_consent import TaskConsentGate
    from ..domain.services.task_digest_guard import TaskDigestGuard
    from ..domain.services.task_ownership import TaskOwnershipRegistry
    from ..server.context import get_context
    from .task_relay_handlers import register_task_relay_handlers

    registry = TaskOwnershipRegistry()
    digest_guard = TaskDigestGuard()
    store = GovernedTaskStore(
        registry=registry,
        digest_guard=digest_guard,
        # Same domain event bus the audit/metrics handlers subscribe to.
        event_publisher=lambda event: get_context().event_bus.publish(event),
    )
    # Fail-close a still-live consent evicted by the gate's TTL/LRU cap: a
    # vanished pending consent must terminally fail the task, never silently hang.
    # The gate hands ``on_evict`` the full consent key
    # ``(target_server_id, task_id, input_key)``; the ledger keys on the task.
    consent_gate = TaskConsentGate(
        on_evict=lambda ck: store.fail_task((ck[0], ck[1]), "consent_unavailable"),
    )

    def _task_upstream_router(
        target_server_id: str,
        method: str,
        params: dict[str, Any],
        timeout: float,
    ) -> dict[str, Any]:
        """Forward a follow-up ``tasks/*`` verbatim to the task's owning upstream.

        Never cold-starts; a missing/unknown server fails closed with a clear
        error (the serving handler surfaces it as a task-relay failure).
        """
        server = get_context().get_mcp_server(target_server_id)
        if server is None:
            raise ValueError(f"unknown target server for relayed task: {target_server_id}")
        return server.relay_request(method, params, timeout)

    # Expose the shared instances on the ApplicationContext so the executor seam
    # resolves the SAME store/gate/router the handlers hold.
    ctx = get_context()
    ctx.governed_task_store = store
    ctx.task_consent_gate = consent_gate
    ctx.task_upstream_router = _task_upstream_router

    register_task_relay_handlers(mcp, store, consent_gate, _task_upstream_router)
    logger.info("governed_tasks_enabled")


def advertise_tasks_capability(mcp: FastMCP, *, relay_tasks_enabled: bool) -> None:
    """Advertise the SEP-2663 Tasks extension so a modern client can discover it.

    Gated on the SAME static kill-switch as handler registration
    (``HAS_NATIVE_TASKS and relay_tasks_enabled``); off, advertised capabilities
    are byte-identical to a plain server.

    Advertised under ``capabilities.extensions``, NOT ``capabilities.tasks``, and
    the difference is not cosmetic -- it decides whether the surface is
    discoverable at all.

    SEP-2663 moved Tasks out of the core capability set into a negotiated
    extension. ``mcp_types.v2026_07_28.ServerCapabilities`` has no ``tasks``
    field, so the SDK's per-version serialization sieve silently DROPS it from a
    2026-07-28 ``server/discover``. Advertising it there produced the exact
    inverse of what was intended:

    * on the legacy handshake, where the field survives, Hangar refuses
      ``tasks/*`` outright (``-32601``) -- advertising what does not run;
    * on the modern wire, where Hangar does serve them, the field was sieved out
      -- running what is not advertised.

    A spec-following 2026-07-28 client therefore could never learn the surface
    existed. Found by the release smoke harness, which reads capabilities from
    ``server/discover`` the way such a client actually would.
    """
    from .._sdk_compat import HAS_NATIVE_TASKS

    if not (HAS_NATIVE_TASKS and relay_tasks_enabled):
        return

    from ..tasks_wire import EXTENSION_ID, TASKS_METHODS

    # The settings name what this server actually serves, so the advertisement
    # and the registration cannot drift: both read TASKS_METHODS. `tasks/list` is
    # absent from that set, so it cannot be advertised by mistake.
    extension_settings: dict[str, Any] = {"methods": sorted(TASKS_METHODS)}

    server = lowlevel_server(mcp)
    original = server.get_capabilities

    def _with_tasks(*args: Any, **kwargs: Any) -> Any:
        capabilities = original(*args, **kwargs)
        extensions = dict(getattr(capabilities, "extensions", None) or {})
        extensions[EXTENSION_ID] = extension_settings
        return capabilities.model_copy(update={"extensions": extensions})

    server.get_capabilities = _with_tasks


__all__ = ["enable_governed_task_relay", "advertise_tasks_capability"]
