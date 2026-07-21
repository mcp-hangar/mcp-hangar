"""Request-path serving surface for relayed governed tasks (ADR-014, Phase 2).

Registers the FOUR v2-native ``tasks/*`` request handlers that let a client
follow up on an already-relayed governed task: poll it (``tasks/get``), fetch
its payload (``tasks/result``), cancel it (``tasks/cancel``) and list its own
(``tasks/list``). Every handler is fail-closed and upstream-truthful:

* **Identity.** On streamable-HTTP the ambient ``identity_context_var`` is not
  propagated into the low-level request handler (the transport runs it in a
  per-session task decoupled from the ASGI auth wrapper), so each handler
  bridges the authenticated principal off the FastMCP request context into the
  contextvar for the duration -- exactly as the ``hangar_call`` batch path does
  (#387). ``asyncio.to_thread`` copies the current context into its worker
  thread, so the bridged identity reaches the (threading-locked) ledger calls.
  An absent principal leaves the caller unattributed, which is fail-closed
  downstream (an unattributed caller can only ever reach unattributed tasks).

* **Composite key.** A client sends only a bare ``task_id``; the ledger is keyed
  on ``(target_server_id, task_id)``. The owning entry is resolved via
  :meth:`GovernedTaskStore.find_owned_key`, which is ownership-fail-closed: a
  ``task_id`` the caller does not own is indistinguishable from one that does not
  exist -- both raise the same ``INVALID_PARAMS`` "Task not found" (no leak).

* **Upstream truth.** State is never fabricated. ``tasks/get`` copies the
  upstream status verbatim; an upstream error leaves the local snapshot
  unchanged. ``tasks/cancel`` retires the entry ONLY when the upstream actually
  confirms cancellation -- otherwise the entry is kept and its TRUE current
  status is returned.

This module is DARK in Phase 2: it is not wired into the server factory yet
(that is the next sub-task) and carries NO consent logic (Phase 4). For an
``input_required`` task ``tasks/get`` simply returns its status as-is.

The upstream transport is injected as ``upstream_router`` so this module depends
only on the router + the ledger (never on the ambient application context); real
wiring routes it through ``get_mcp_server(target_server_id).relay_request(...)``
and tests inject a fake.
"""

from __future__ import annotations

import asyncio
from typing import Any

# The constructed result classes are sourced directly from ``mcp_types`` (a hard
# v2 dependency here -- this serving surface is native-tasks-only). ``_sdk_compat``
# re-exports them as ``X | None`` via ``getattr`` so it can import on either SDK
# generation; that Optional trips a "None not callable" at each constructor. The
# concrete import keeps mypy honest without per-call ignores, mirroring the v2
# branch of ``flat_tool_projection``.
from mcp_types import CancelTaskResult, GetTaskResult, ListTasksResult

from mcp_hangar._sdk_compat import (
    INVALID_PARAMS,
    CallToolResult,
    CancelTaskRequestParams,
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    PaginatedRequestParams,
    lowlevel_server,
    make_mcp_error,
)
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# The relay is a thin transport forward; mirror ``relay_request``'s default.
_RELAY_TIMEOUT = 30.0

# Injected upstream transport: (target_server_id, method, params, timeout) -> raw
# JSON-RPC response dict (the ``{"result": ...}`` / ``{"error": ...}`` shape).
UpstreamRouter = Any


def register_task_relay_handlers(
    mcp: Any,
    store: GovernedTaskStore,
    upstream_router: UpstreamRouter,
) -> None:
    """Register the four ``tasks/*`` serving handlers on the low-level MCP server.

    Args:
        mcp: The FastMCP/MCPServer instance whose low-level server receives the
            handlers.
        store: The governance ledger authorizing + snapshotting relayed tasks.
        upstream_router: Callable ``(target_server_id, method, params, timeout)``
            returning the raw upstream JSON-RPC response dict. Injected so this
            module never reaches into the ambient application context.
    """
    low = lowlevel_server(mcp)

    def _bridge_identity(ctx: Any) -> Any:
        """Bridge the request's authenticated principal into ``identity_context_var``.

        Returns a contextvar token to reset (or ``None`` when nothing was set).
        Only bridges when no identity is already bound -- never clobbering an
        identity the ASGI wrapper legitimately propagated (stdio/local). Fully
        fault-barriered: any failure leaves identity untouched (unattributed →
        fail-closed downstream).
        """
        if get_identity_context() is not None:
            return None
        try:
            state = getattr(getattr(getattr(ctx, "request_context", None), "request", None), "state", None)
            principal = getattr(getattr(state, "auth", None), "principal", None)
            if principal is not None:
                return identity_context_var.set(_principal_to_identity_context(principal))
        except Exception:  # noqa: BLE001 -- identity bridging must never break the serving path
            return None
        return None

    async def _resolve_owned_key(task_id: str) -> tuple[str, str]:
        """Resolve the composite key for ``task_id`` the caller owns, else deny.

        Denial raises ``INVALID_PARAMS`` "Task not found" with no existence leak.
        """
        key = await asyncio.to_thread(store.find_owned_key, task_id)
        if key is None:
            raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
        return key

    async def _list(ctx: Any, params: Any) -> Any:
        """``tasks/list``: return the caller's owned snapshots as one page.

        The inner/upstream cursor is never forwarded (it could identify another
        tenant's task); ``nextCursor`` is therefore always absent.
        """
        token = _bridge_identity(ctx)
        try:
            tasks, _ = await asyncio.to_thread(store.list_tasks)
            return ListTasksResult(tasks=tasks)
        finally:
            if token is not None:
                identity_context_var.reset(token)

    async def _get(ctx: Any, params: Any) -> Any:
        """``tasks/get``: relay to the owning upstream, sync the snapshot, return it flat.

        An upstream error returns the local snapshot unchanged (no fabrication).
        A ``working -> completed`` transition emits ``TaskCompleted`` exactly once
        (dedup is atomic inside the store). No consent/``input_required`` handling
        here -- Phase 4 wires that; an ``input_required`` status is returned as-is.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            key = await _resolve_owned_key(task_id)
            target_server_id = key[0]
            if not await asyncio.to_thread(store.authorize, key):
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")

            resp = await asyncio.to_thread(
                upstream_router, target_server_id, "tasks/get", {"task_id": task_id}, _RELAY_TIMEOUT
            )
            if not (isinstance(resp, dict) and "error" in resp):
                result = resp.get("result") if isinstance(resp, dict) else None
                if isinstance(result, dict):
                    status = result.get("status")
                    status_message = result.get("statusMessage", result.get("status_message"))
                    if status == "completed":
                        # Owner-emitted, deduped working->completed transition.
                        await asyncio.to_thread(store.mark_completed, key, status_message)
                    elif status is not None:
                        await asyncio.to_thread(store.update_snapshot, key, status, status_message)

            snapshot = await asyncio.to_thread(store.get_task, key)
            if snapshot is None:
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
            return GetTaskResult(**snapshot.model_dump(by_alias=False))
        finally:
            if token is not None:
                identity_context_var.reset(token)

    async def _result(ctx: Any, params: Any) -> Any:
        """``tasks/result`` (wire ``tasks/result``): re-verify the pin, relay, reconstruct.

        Re-verifies the pinned tool digest fail-closed (drift fails the task and
        raises -- the ``McpError`` propagates). Reconstructs the payload by
        validating the upstream ``result`` into ``CallToolResult`` (the marker for
        a bespoke result type is empty until Phase 3, so ``CallToolResult`` is the
        default). An upstream error surfaces as ``INVALID_PARAMS``.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            key = await _resolve_owned_key(task_id)
            if not await asyncio.to_thread(store.authorize, key):
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")

            # Fail-closed supply-chain re-verification; its McpError propagates.
            await asyncio.to_thread(store._verify_pinned_digest, key)

            resp = await asyncio.to_thread(
                upstream_router, key[0], "tasks/result", {"task_id": task_id}, _RELAY_TIMEOUT
            )
            if isinstance(resp, dict) and "error" in resp:
                raise make_mcp_error(INVALID_PARAMS, "task result unavailable")
            result = resp.get("result") if isinstance(resp, dict) else None
            return CallToolResult.model_validate(result)
        finally:
            if token is not None:
                identity_context_var.reset(token)

    async def _cancel(ctx: Any, params: Any) -> Any:
        """``tasks/cancel``: best-effort relay; retire ONLY on a confirmed cancel.

        Cancellation is confirmed only by a clean upstream ``result`` whose status
        is ``cancelled`` (or absent) and no ``error``. On confirmation the entry is
        marked cancelled (emitting ``TaskCancelled`` once) and retired. On an
        upstream error -- or a result still reporting a non-cancelled status -- the
        entry is KEPT and its TRUE current status is returned (never fabricated).
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            key = await _resolve_owned_key(task_id)
            if not await asyncio.to_thread(store.authorize, key):
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")

            resp = await asyncio.to_thread(
                upstream_router, key[0], "tasks/cancel", {"task_id": task_id}, _RELAY_TIMEOUT
            )
            if _cancel_confirmed(resp):
                snapshot = await asyncio.to_thread(store.mark_cancelled, key)
                await asyncio.to_thread(store.delete_task, key)
                if snapshot is None:
                    raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
                data = snapshot.model_dump(by_alias=False)
                data["status"] = "cancelled"
                return CancelTaskResult(**data)

            # Not confirmed: keep the entry, return the TRUE current status.
            snapshot = await asyncio.to_thread(store.get_task, key)
            if snapshot is None:
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
            return CancelTaskResult(**snapshot.model_dump(by_alias=False))
        finally:
            if token is not None:
                identity_context_var.reset(token)

    # Byte-exact wire method strings. ``tasks/result`` fetches the payload
    # (GetTaskPayloadRequestParams). NO ``tasks/update`` handler (out of scope).
    low.add_request_handler("tasks/get", GetTaskRequestParams, _get)
    low.add_request_handler("tasks/result", GetTaskPayloadRequestParams, _result)
    low.add_request_handler("tasks/cancel", CancelTaskRequestParams, _cancel)
    low.add_request_handler("tasks/list", PaginatedRequestParams, _list)


def _cancel_confirmed(resp: Any) -> bool:
    """Does a raw upstream response CONFIRM cancellation?

    Confirmed iff it is a clean result (a ``result`` present, no ``error``) whose
    status is either ``cancelled`` or absent. An ``error`` response, a missing
    ``result``, or a result still reporting a non-cancelled status is NOT a
    confirmation (the entry must be kept and its true status returned).
    """
    if not isinstance(resp, dict) or "error" in resp or "result" not in resp:
        return False
    result = resp.get("result")
    if isinstance(result, dict):
        status = result.get("status")
        return status is None or status == "cancelled"
    # A clean non-dict result (2xx-equivalent) with no contradicting status.
    return result is not None


__all__ = ["register_task_relay_handlers"]
