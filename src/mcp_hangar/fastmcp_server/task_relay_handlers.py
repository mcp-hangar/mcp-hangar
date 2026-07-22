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

Phase 4 (ADR-014, #322) adds SYNCHRONOUS mid-flight consent, behind the same
kill-switch (dark by default). On the 2025-11-25 protocol there is no inbound
``tasks/update``: when a relayed task's upstream status is ``input_required``,
``tasks/get`` resolves it in-handler -- eliciting the downstream client for
consent via ``ctx.session`` and relaying the answer upstream. Consent is
obtained BEFORE the gate opens (open-only-on-accept, no race); every non-accept
outcome (decline/cancel/no-back-channel/error/missing capability) terminally
FAILS the task fail-closed, so a paused task is never left hanging.

The upstream transport is injected as ``upstream_router`` so this module depends
only on the router + the ledger (never on the ambient application context); real
wiring routes it through ``get_mcp_server(target_server_id).relay_request(...)``
and tests inject a fake.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

# The constructed result classes are sourced directly from ``mcp_types`` (a hard
# v2 dependency here -- this serving surface is native-tasks-only). ``_sdk_compat``
# re-exports them as ``X | None`` via ``getattr`` so it can import on either SDK
# generation; that Optional trips a "None not callable" at each constructor. The
# concrete import keeps mypy honest without per-call ignores, mirroring the v2
# branch of ``flat_tool_projection``.
from mcp_types import CancelTaskResult, GetTaskResult

try:  # ``ListTasksResult`` is removed in 2026-07-28 (SEP-2663); guard the import.
    from mcp_types import ListTasksResult
except ImportError:  # pragma: no cover -- b3+ where tasks/list is gone
    ListTasksResult = None  # type: ignore[assignment,misc]

from mcp_hangar._sdk_compat import (
    DEFAULT_NEGOTIATED_VERSION,
    HAS_LIST_TASKS,
    HAS_TASKS_UPDATE,
    INVALID_PARAMS,
    CallToolResult,
    CancelTaskRequestParams,
    GetTaskPayloadRequestParams,
    GetTaskRequestParams,
    PaginatedRequestParams,
    UpdateTaskRequestParams,
    UpdateTaskResult,
    lowlevel_server,
    make_mcp_error,
)
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# The relay is a thin transport forward; mirror ``relay_request``'s default.
_RELAY_TIMEOUT = 30.0

# Injected upstream transport: (target_server_id, method, params, timeout) -> raw
# JSON-RPC response dict (the ``{"result": ...}`` / ``{"error": ...}`` shape).
UpstreamRouter = Any

# The 2026-07-28 protocol resolves task input via ``InputRequiredResult.input_requests``
# + an inbound ``tasks/update`` handler. THIS serving surface targets the 2025-11-25
# session, where input is resolved SYNCHRONOUSLY (elicit the downstream client, then
# relay the answer upstream). The modern path is guarded on this version and stays
# unreachable here (finding #8); ISO-date strings compare correctly lexicographically.
_MODERN_TASKS_VERSION = "2026-07-28"

# Form-mode consent prompt + schema for the synchronous 2025-11-25 resolution. The
# empty-object schema requests a bare accept/decline/cancel confirmation (no fields).
_CONSENT_PROMPT = (
    "Task {task_id} on an upstream server is requesting additional input to continue. Do you consent to providing it?"
)
_CONSENT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


def _current_principal_id() -> str:
    """The current caller's principal id (user_id, else agent_id), else ``""``."""
    identity = get_identity_context()
    if identity is None or identity.caller is None:
        return ""
    caller = identity.caller
    return caller.user_id or caller.agent_id or ""


def _is_modern_tasks_session(ctx: Any) -> bool:
    """Does this session speak the 2026-07-28+ modern tasks-input protocol?

    Fail-safe to the SYNCHRONOUS 2025-11-25 path: any missing/garbled version is
    treated as pre-modern. Returns ``False`` on a 2025-11-25 session, keeping the
    modern branch unreachable on this serving surface (finding #8).
    """
    version = getattr(getattr(ctx, "session", None), "protocol_version", DEFAULT_NEGOTIATED_VERSION)
    try:
        return str(version) >= _MODERN_TASKS_VERSION
    except Exception:  # noqa: BLE001 -- a non-comparable version is treated as pre-modern
        return False


def _client_supports_elicitation(ctx: Any) -> bool:
    """Fail-closed: did the downstream client negotiate the elicitation capability?

    Consent is obtained via ``elicit_form``, so an absent elicitation capability
    means there is NO back-channel to consent the caller -- fail-closed (finding
    #9). Reads the negotiated capabilities off ``ctx.session.client_params``; any
    missing/None link in the chain -> ``False``.
    """
    try:
        caps = getattr(getattr(getattr(ctx, "session", None), "client_params", None), "capabilities", None)
        return getattr(caps, "elicitation", None) is not None
    except Exception:  # noqa: BLE001 -- capability probing must never break the serving path
        return False


def _derive_input_key(result: dict[str, Any]) -> str:
    """Derive a DETERMINISTIC consent key for a task's pending input request(s).

    Stable across repeated polls of the same ``input_required`` state so a
    concurrent second ``tasks/get`` maps to the SAME gate key (enabling the
    reprompt guard, finding #6). When the upstream result carries a structured
    ``inputRequests`` map (the extension shape the gate documents), the key
    digests its server-assigned request ids in sorted order; otherwise it digests
    the verbatim upstream ``statusMessage``. Always non-empty (the gate rejects
    empty keys).
    """
    reqs = result.get("inputRequests")
    if not isinstance(reqs, dict):
        reqs = result.get("input_requests")
    if isinstance(reqs, dict) and reqs:
        basis = "ids:" + json.dumps(sorted(reqs.keys()), separators=(",", ":"))
    else:
        message = result.get("statusMessage") or result.get("status_message") or ""
        basis = "msg:" + str(message)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def register_task_relay_handlers(
    mcp: Any,
    store: GovernedTaskStore,
    consent_gate: TaskConsentGate,
    upstream_router: UpstreamRouter,
) -> None:
    """Register the four ``tasks/*`` serving handlers on the low-level MCP server.

    Args:
        mcp: The FastMCP/MCPServer instance whose low-level server receives the
            handlers.
        store: The governance ledger authorizing + snapshotting relayed tasks.
        consent_gate: The fail-closed presence gate for mid-flight ``input_required``
            consent (ADR-014 Phase 4). Opened ONLY after a downstream accept.
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

    async def _sync_snapshot_from_result(key: tuple[str, str], result: dict[str, Any]) -> None:
        """Sync the local snapshot from a raw upstream ``tasks/get`` result dict."""
        status = result.get("status")
        status_message = result.get("statusMessage", result.get("status_message"))
        if status == "completed":
            # Owner-emitted, deduped working->completed transition.
            await asyncio.to_thread(store.mark_completed, key, status_message)
        elif status is not None:
            await asyncio.to_thread(store.update_snapshot, key, status, status_message)

    async def _flat_snapshot(key: tuple[str, str], task_id: str) -> Any:
        """Return the authorized snapshot as a flat ``GetTaskResult``, else deny."""
        snapshot = await asyncio.to_thread(store.get_task, key)
        if snapshot is None:
            raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
        return GetTaskResult(**snapshot.model_dump(by_alias=False))

    async def _deny_consent(key: tuple[str, str], task_id: str, input_key: str, principal_id: str) -> Any:
        """Terminal fail-closed denial (findings #1/#9, D6 never-hang).

        Fails the task closed, best-effort relays ``tasks/cancel`` upstream,
        discards any gate presence, records the negative decision, and returns
        the (now ``failed``) snapshot -- NEVER leaving the task ``input_required``.
        """
        await asyncio.to_thread(store.fail_task, key, "consent_denied")
        try:  # best-effort upstream cancel; never blocks the terminal resolution
            await asyncio.to_thread(upstream_router, key[0], "tasks/cancel", {"task_id": task_id}, _RELAY_TIMEOUT)
        except Exception:  # noqa: BLE001 -- upstream cancel is strictly best-effort
            logger.debug("consent_denied_upstream_cancel_failed", target_server_id=key[0], task_id=task_id)
        consent_gate.discard(key)
        await asyncio.to_thread(store.record_consent_decision, key, input_key, False, principal_id)
        return await _flat_snapshot(key, task_id)

    async def _grant_consent(key: tuple[str, str], task_id: str, input_key: str, principal_id: str) -> Any:
        """Accepted-consent path: open the gate NOW, relay the answer, re-sync.

        The gate opens ONLY here, after a confirmed accept (finding #1 -- no
        pre-decision race). The answer is relayed upstream idempotently; the
        consent is CONSUMED only after a confirmed successful relay. A transient
        relay failure discards the gate WITHOUT consuming so a retry re-elicits
        and completes (finding #3 -- recoverable), and does NOT fail the task.
        """
        consent_gate.open(key, input_key)
        # Relay the consented answer upstream. The upstream answer method mirrors
        # the gate's ``tasks/update`` answer vocabulary; on 2025-11-25 this is the
        # server->upstream forward, distinct from the (never-registered) inbound
        # downstream ``tasks/update`` handler.
        answer_resp = await asyncio.to_thread(
            upstream_router, key[0], "tasks/update", {"task_id": task_id, "input_key": input_key}, _RELAY_TIMEOUT
        )
        if isinstance(answer_resp, dict) and "error" in answer_resp:
            # Transient upstream refusal: leave recoverable. Discard the gate (do
            # NOT consume via answer()) so a retry re-elicits + re-relays; the task
            # stays live (not failed) and the caller can poll again.
            consent_gate.discard(key)
            raise make_mcp_error(INVALID_PARAMS, "consent answer relay failed; retry")
        # Confirmed relay -> consume the single-use consent + record provenance.
        consent_gate.answer(key, input_key)
        await asyncio.to_thread(store.record_consent_decision, key, input_key, True, principal_id)
        # Re-relay tasks/get to reflect the post-input upstream status.
        resp = await asyncio.to_thread(upstream_router, key[0], "tasks/get", {"task_id": task_id}, _RELAY_TIMEOUT)
        if not (isinstance(resp, dict) and "error" in resp):
            result = resp.get("result") if isinstance(resp, dict) else None
            if isinstance(result, dict):
                await _sync_snapshot_from_result(key, result)
        return await _flat_snapshot(key, task_id)

    async def _consent_for_input_required(ctx: Any, key: tuple[str, str], task_id: str, result: dict[str, Any]) -> Any:
        """Synchronously obtain downstream consent for a task's mid-flight input.

        2025-11-25 has no inbound ``tasks/update``: the pending input is resolved
        in-handler by eliciting the downstream client for consent (tenant was
        already authorized above -- structurally above the gate), then relaying
        the answer upstream. Consent is obtained BEFORE the gate opens (finding
        #1); every non-accept outcome terminally fails the task (D6 never-hang).
        """
        if _is_modern_tasks_session(ctx):
            # 2026-07-28 (SEP-2663): the client resolves ``input_required`` by driving
            # an inbound ``tasks/update`` (governed in ``_update`` below), NOT by a
            # synchronous elicit here. Pass the ``input_required`` snapshot through
            # untouched -- upstream-truthful -- and let the client come back through
            # the governed update path. Unreachable on a 2025-11-25 session (the flag
            # is fail-safe to pre-modern), so b2 behavior is byte-identical.
            return await _flat_snapshot(key, task_id)

        input_key = _derive_input_key(result)
        principal_id = _current_principal_id()

        # Concurrent-reprompt guard (finding #6): a consent already pending for
        # this exact (key, input_key) means another _get is mid-flight -- do NOT
        # re-prompt or double-relay; return the current (still input_required) snapshot.
        if consent_gate.is_consent_pending(key, input_key):
            return await _flat_snapshot(key, task_id)

        # (1) No downstream elicitation channel -> immediate fail-closed (finding #9).
        if not _client_supports_elicitation(ctx):
            return await _deny_consent(key, task_id, input_key, principal_id)

        # (2) Obtain the decision BEFORE opening the gate. Catch ANY elicitation
        #     failure (not just NoBackChannelError) -> fail-closed (finding #9).
        try:
            decision = await ctx.session.elicit_form(_CONSENT_PROMPT.format(task_id=task_id), _CONSENT_SCHEMA)
        except Exception:  # noqa: BLE001 -- any elicitation failure is a fail-closed denial
            return await _deny_consent(key, task_id, input_key, principal_id)

        # (3) accept -> open gate + relay; (4) decline/cancel/other -> fail-closed.
        if getattr(decision, "action", None) == "accept":
            return await _grant_consent(key, task_id, input_key, principal_id)
        return await _deny_consent(key, task_id, input_key, principal_id)

    async def _get(ctx: Any, params: Any) -> Any:
        """``tasks/get``: relay to the owning upstream, sync the snapshot, return it flat.

        An upstream error returns the local snapshot unchanged (no fabrication).
        A ``working -> completed`` transition emits ``TaskCompleted`` exactly once
        (dedup is atomic inside the store). An ``input_required`` status triggers
        the synchronous Phase-4 consent flow (elicit downstream, relay upstream);
        every denial terminally fails the task (never left ``input_required``).
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
                    await _sync_snapshot_from_result(key, result)
                    if result.get("status") == "input_required":
                        return await _consent_for_input_required(ctx, key, task_id, result)

            return await _flat_snapshot(key, task_id)
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

    async def _update(ctx: Any, params: Any) -> Any:
        """``tasks/update`` (2026-07-28 / SEP-2663): the GOVERNED modern input path.

        On the modern protocol the downstream client resolves a task's mid-flight
        ``input_required`` by driving an inbound ``tasks/update`` carrying its input
        -- so THIS handler, not ``tasks/get``, is where consent is governed on a
        modern session (the 2025-11-25 surface governs synchronously in ``_get``).
        An inbound update IS the client's consent to provide input: authorize the
        tenant, gate the decision on the composite key, relay the client's payload
        upstream verbatim (upstream-truthful), record the decision, and re-sync.

        A transient upstream refusal discards the gate WITHOUT consuming (finding
        #3 -- recoverable) and raises; it does not fail the task. Registered ONLY
        when the SDK defines ``UpdateTaskRequest`` (HAS_TASKS_UPDATE), so it is
        never built on the b2 RC beta -- b2 behavior is byte-identical.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            key = await _resolve_owned_key(task_id)
            if not await asyncio.to_thread(store.authorize, key):
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
            principal_id = _current_principal_id()

            # Key the decision off the current upstream input_required state.
            probe = await asyncio.to_thread(upstream_router, key[0], "tasks/get", {"task_id": task_id}, _RELAY_TIMEOUT)
            probed = probe.get("result") if isinstance(probe, dict) else None
            input_key = _derive_input_key(probed if isinstance(probed, dict) else {})

            # Consent BEFORE the answer reaches upstream (finding #1). Relay the
            # client's payload verbatim; consume only on a confirmed relay.
            consent_gate.open(key, input_key)
            payload = params.model_dump(by_alias=True) if hasattr(params, "model_dump") else {"task_id": task_id}
            resp = await asyncio.to_thread(upstream_router, key[0], "tasks/update", payload, _RELAY_TIMEOUT)
            if isinstance(resp, dict) and "error" in resp:
                consent_gate.discard(key)  # recoverable: retry re-drives the update
                raise make_mcp_error(INVALID_PARAMS, "task update relay failed; retry")
            consent_gate.answer(key, input_key)
            await asyncio.to_thread(store.record_consent_decision, key, input_key, True, principal_id)

            updated = resp.get("result") if isinstance(resp, dict) else None
            if isinstance(updated, dict):
                await _sync_snapshot_from_result(key, updated)
            snapshot = await asyncio.to_thread(store.get_task, key)
            if snapshot is None:
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")
            # ``UpdateTaskResult`` shape is finalized with the b3 SDK (Tier C); fall
            # back to the flat ``GetTaskResult`` projection until it lands.
            result_cls = UpdateTaskResult or GetTaskResult
            return result_cls(**snapshot.model_dump(by_alias=False))
        finally:
            if token is not None:
                identity_context_var.reset(token)

    # Byte-exact wire method strings. ``tasks/result`` fetches the payload
    # (GetTaskPayloadRequestParams). ``tasks/list`` (removed in 2026-07-28) and the
    # inbound ``tasks/update`` (added in 2026-07-28) are each registered only while
    # the SDK defines their type -- so the served surface tracks the negotiated
    # protocol without a version bump (advertise/serve exactly what runs).
    low.add_request_handler("tasks/get", GetTaskRequestParams, _get)
    low.add_request_handler("tasks/result", GetTaskPayloadRequestParams, _result)
    low.add_request_handler("tasks/cancel", CancelTaskRequestParams, _cancel)
    if HAS_LIST_TASKS:
        low.add_request_handler("tasks/list", PaginatedRequestParams, _list)
    if HAS_TASKS_UPDATE:
        low.add_request_handler("tasks/update", UpdateTaskRequestParams, _update)


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
