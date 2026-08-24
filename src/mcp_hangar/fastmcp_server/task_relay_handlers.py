"""Request-path serving surface for relayed governed tasks (ADR-014, Phase 2).

Registers the THREE methods SEP-2663 defines: poll a relayed governed task
(``tasks/get``), answer its mid-flight input (``tasks/update``) and cancel it
(``tasks/cancel``). Every handler is fail-closed and upstream-truthful:

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
  unchanged and returns it with no outcome fields. ``tasks/cancel`` retires the
  ledger entry ONLY when the upstream actually confirms cancellation, and its
  acknowledgement is empty either way -- SEP-2663 makes cancellation
  cooperative, so claiming a status the upstream never reported would be the
  fabrication the spec warns about.

## Who is served, and what everyone else gets

SEP-2663 splits refusal into two codes, and the split is deliberate:

===========================================  =========================================
Caller                                       ``tasks/*``
===========================================  =========================================
2026-07-28 client declaring the extension    served
2026-07-28 client NOT declaring it           ``-32021`` + ``requiredCapabilities``
2025-11-25 (or older) connection             ``-32601`` method not found
declaring client, unknown/expired task id    ``-32602`` invalid params
===========================================  =========================================

A modern client can fix its declaration and retry, so it is told *what* to
declare. A legacy connection cannot, so for it these methods simply do not
exist. ``tasks/result`` and ``tasks/list`` get ``-32601`` from every caller --
SEP-2663 removes both, and an unregistered method already produces exactly that
code, so their absence here IS the implementation.

The 2025-11-25 Tasks feature is deliberately NOT served. It was removed from
the core spec in 2026-07-28 and its shapes live on only in ``mcp_types``, which
ADR-015 bars from any serving path. Serving those shapes to a legacy client
would be the same class of defect as serving them to a modern one, just aimed
the other way.

## Mid-flight consent

Phase 4 (ADR-014, #322) governs a task's mid-flight ``input_required`` through
the inbound ``tasks/update`` handler: an update IS the client's consent to
provide input. The gate opens BEFORE the answer reaches upstream and the consent
is CONSUMED only on a confirmed relay, so a transient upstream refusal leaves the
task recoverable rather than failed.

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

# Results are the VENDORED SEP-2663 shapes, never `mcp_types.Task*` (ADR-015).
# `mcp_types` still carries the SEP-1686 generation -- nested `CreateTaskResult`,
# `ttl`, `pollInterval`, no `resultType` -- and serving those to a 2026-07-28
# client is the defect this module was realigned to remove.
from mcp.shared.inbound import decode_header_value

from mcp_hangar._sdk_compat import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    RequestParams,
    lowlevel_server,
    make_mcp_error,
)
from mcp_hangar.tasks_wire import (
    EXTENSION_ID,
    HEADER_MISMATCH,
    MCP_NAME_HEADER,
    MISSING_REQUIRED_CLIENT_CAPABILITY,
    EmptyResult,
    GetTaskResult,
    missing_capability_error_data,
)
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context
from mcp_hangar.fastmcp_server.resource_link_read_through import project_result_uris
from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# The relay is a thin transport forward; mirror ``relay_request``'s default.
_RELAY_TIMEOUT = 30.0

# Injected upstream transport: (target_server_id, method, params, timeout) -> raw
# JSON-RPC response dict (the ``{"result": ...}`` / ``{"error": ...}`` shape).
UpstreamRouter = Any

# SEP-2663 is a 2026-07-28 extension. Below this version the methods do not
# exist; ISO-date strings compare correctly lexicographically.
_MODERN_TASKS_VERSION = "2026-07-28"


class _GetTaskParams(RequestParams):
    """``tasks/get`` params.

    Subclasses the SDK's ``RequestParams`` rather than the vendored
    ``tasks_wire`` model on purpose: the SDK parses ``_meta`` off this base, and
    ``_update`` forwards its dump upstream verbatim -- a model without ``_meta``
    would silently drop the progress token and the reserved
    ``io.modelcontextprotocol/*`` keys on the way through. ``RequestParams`` is
    the generic request base, not one of the ``Task*`` fossils ADR-015 bars, and
    it supplies the camelCase aliasing (``task_id`` -> ``taskId``) for free.

    The field sets are pinned against ``tasks_wire`` by test, so the two
    definitions cannot drift.
    """

    task_id: str


class _CancelTaskParams(RequestParams):
    """``tasks/cancel`` params. See :class:`_GetTaskParams` for why this base."""

    task_id: str


class _UpdateTaskParams(RequestParams):
    """``tasks/update`` params: answers keyed by the snapshot's ``inputRequests``.

    ``input_responses`` is required. SEP-2663 says answers naming an input
    request that was never issued are *ignored*, but a call carrying no answers
    at all is a malformed request rather than an empty no-op -- so a missing map
    is rejected by validation (``-32602``) before the handler runs.
    """

    task_id: str
    input_responses: dict[str, Any]


def _current_principal_id() -> str:
    """The current caller's principal id (user_id, else agent_id), else ``""``."""
    identity = get_identity_context()
    if identity is None or identity.caller is None:
        return ""
    caller = identity.caller
    return caller.user_id or caller.agent_id or ""


def _is_modern_tasks_session(ctx: Any) -> bool:
    """Does this connection speak 2026-07-28, where SEP-2663 Tasks exist at all?

    Fail-closed: any missing or non-comparable version is treated as legacy, so
    an unreadable session gets ``-32601`` rather than a modern-shaped reply.
    """
    version = getattr(getattr(ctx, "session", None), "protocol_version", None)
    if version is None:
        return False
    try:
        return str(version) >= _MODERN_TASKS_VERSION
    except Exception:  # noqa: BLE001 -- a non-comparable version is treated as legacy
        return False


def _client_declared_tasks_extension(ctx: Any) -> bool:
    """Fail-closed: did the client declare ``io.modelcontextprotocol/tasks``?

    SEP-2663 gates the whole surface on the client having declared the extension
    at initialize. Reads the negotiated capabilities off
    ``ctx.session.client_params``; any missing/None link in the chain, or a
    capabilities object that cannot be inspected, counts as *not declared*.

    Accepts either the parsed model (``capabilities.extensions``) or a plain
    mapping, since the SDK's capability object shape differs across the
    generations this code has to run on.
    """
    try:
        caps = getattr(getattr(getattr(ctx, "session", None), "client_params", None), "capabilities", None)
        extensions = getattr(caps, "extensions", None)
        if extensions is None and isinstance(caps, dict):
            extensions = caps.get("extensions")
        if isinstance(extensions, dict):
            return EXTENSION_ID in extensions
        return extensions is not None and hasattr(extensions, EXTENSION_ID)
    except Exception:  # noqa: BLE001 -- capability probing must never break the serving path
        return False


def _require_mcp_name_header(ctx: Any, task_id: str) -> None:
    """Enforce SEP-2663's mandatory ``Mcp-Name: <taskId>`` on ``tasks/*``.

    SEP-2663 requires the header (via SEP-2243) so an intermediary can route a
    poll to the instance holding the task's state **without parsing the body**.
    That only works if it is actually always there, so a missing one is refused
    rather than tolerated.

    The SDK does not do this for us. Its ``NAME_BEARING_METHODS`` covers
    ``tools/call`` / ``prompts/get`` / ``resources/read`` only, and even for
    those it checks *agreement* (`if body_value is not None`), never presence.
    Hangar's own front-door middleware cannot cover it either: that middleware
    deliberately disengages on 2026-07-28 (`_should_engage`), which is precisely
    the generation `tasks/*` exist in. This handler gate is the only rung that
    runs.

    Only enforced over HTTP. SEP-2663 scopes the requirement to Streamable
    HTTP, and on stdio there are no headers to carry it -- so an absent request
    object means "not applicable", not "missing header".
    """
    request = getattr(ctx, "request", None) or getattr(getattr(ctx, "request_context", None), "request", None)
    if request is None:
        return

    headers = getattr(request, "headers", None)
    if headers is None:
        return

    try:
        raw_header = headers.get(MCP_NAME_HEADER)
    except Exception:  # noqa: BLE001 -- an unreadable header bag is not the caller's fault
        return

    if not raw_header:
        raise make_mcp_error(
            HEADER_MISMATCH,
            f"{MCP_NAME_HEADER} header is required on tasks/* requests (SEP-2663)",
        )
    header_value = decode_header_value(raw_header)
    if header_value is None or header_value != task_id:
        # Malformed sentinel or a value that disagrees with the body: an
        # intermediary that routed on the header sent this request somewhere
        # the body did not ask for. Decode through the SDK codec so a
        # conforming ``=?base64?…?=`` wrapper is not itself a mismatch.
        raise make_mcp_error(
            HEADER_MISMATCH,
            f"{MCP_NAME_HEADER} header does not match the request body's taskId",
        )


def _require_tasks_client(ctx: Any, task_id: str) -> None:
    """Refuse callers SEP-2663 says must be refused, with the code it specifies.

    The order is the contract, not an implementation detail:

    1. **Version.** On a legacy connection these methods do not exist. Telling
       such a client to "declare an extension" (``-32021``) would point it at a
       capability its protocol generation cannot negotiate.
    2. **Routing header.** Checked before the capability, because it is a
       property of the *request* rather than of the client's declaration -- a
       misrouted request should be rejected as misrouted whatever the client
       declared, and the answer must not depend on how far down the ladder it
       happens to get.
    3. **Capability.** Only now is "you did not declare the extension" the
       actionable answer.
    """
    if not _is_modern_tasks_session(ctx):
        raise make_mcp_error(METHOD_NOT_FOUND, "Method not found")
    _require_mcp_name_header(ctx, task_id)
    if not _client_declared_tasks_extension(ctx):
        raise make_mcp_error(
            MISSING_REQUIRED_CLIENT_CAPABILITY,
            f"Client must declare the {EXTENSION_ID} extension to use tasks/*",
            data=missing_capability_error_data(),
        )


def _derive_input_key(result: dict[str, Any]) -> str:
    """Derive a DETERMINISTIC consent key for a task's pending input request(s).

    Stable across repeated polls of the same ``input_required`` state so a
    concurrent second ``tasks/get`` maps to the SAME gate key (enabling the
    reprompt guard, finding #6). When the upstream result carries a structured
    ``inputRequests`` map (the extension shape the gate documents), the key
    digests its server-assigned request ids in sorted order; otherwise it digests
    the verbatim upstream ``statusMessage``. Always non-empty (the gate rejects
    empty keys).

    **The request values -- including any ``method`` on them -- are deliberately
    not read.** It looks like an oversight and is not. The key must be identical
    across repeated polls of one paused state; the request *ids* are what the
    upstream holds stable, while the values are free to be reworded between
    polls. Digesting a ``method`` would also couple the gate key to a field
    SEP-2663 is still reshaping (see ``approvals/pending.py`` and
    modelcontextprotocol#2919), so re-expressing the same pause under a different
    method identifier would silently mint a new key and defeat the reprompt
    guard. Identity of the pending request, not its description.
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


def register_task_relay_handlers(  # noqa: C901 -- baseline CC=33; split before extending
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

        The request lives at ``ctx.request`` on SDK v2 and at
        ``ctx.request_context.request`` on v1. Reading only the v1 spelling made
        this a silent no-op on v2: the shipped ``serve --http`` path binds the
        principal on ``request.state.auth`` and NOT on ``identity_context_var``
        (unlike the factory's ASGI wrapper), so with auth enabled every
        ``tasks/*`` call was unattributed and an owner could not reach their own
        task -- fail-closed, but the relay was dead in the deployment mode that
        matters.
        """
        if get_identity_context() is not None:
            return None
        try:
            request = getattr(ctx, "request", None) or getattr(getattr(ctx, "request_context", None), "request", None)
            state = getattr(request, "state", None)
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

    async def _sync_snapshot_from_result(key: tuple[str, str], result: dict[str, Any]) -> None:
        """Sync the local snapshot from a raw upstream ``tasks/get`` result dict."""
        status = result.get("status")
        status_message = result.get("statusMessage", result.get("status_message"))
        if status == "completed":
            # Owner-emitted, deduped working->completed transition.
            await asyncio.to_thread(store.mark_completed, key, status_message)
        elif status is not None:
            await asyncio.to_thread(store.update_snapshot, key, status, status_message)

    async def _flat_snapshot(
        key: tuple[str, str],
        task_id: str,
        *,
        upstream: dict[str, Any] | None = None,
    ) -> Any:
        """Project the authorized snapshot into the SEP-2663 ``GetTaskResult``.

        The ledger still stores snapshots as the SEP-1686 ``mcp_types.Task``, so
        this is the boundary where the two namings meet. It is a pure RENAME,
        not a lossy hop: the fossil documents ``ttl`` as "retention duration ...
        in milliseconds" and ``poll_interval`` as "Suggested polling interval in
        milliseconds", which is exactly what ``ttlMs`` / ``pollIntervalMs`` mean.
        Moving the ledger onto the vendored type is a separate change.

        ``upstream`` carries the raw upstream ``tasks/get`` result when there was
        one, so a task's outcome and its ``inputRequests`` reach the client
        inlined -- the whole point of SEP-2663 folding ``tasks/result`` into the
        poll. Absent it (upstream error, or a snapshot-only read), the outcome
        fields stay ``None`` rather than being fabricated.
        """
        snapshot = await asyncio.to_thread(store.get_task, key)
        if snapshot is None:
            raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")

        data = snapshot.model_dump(by_alias=False)
        projected: dict[str, Any] = {
            "task_id": data["task_id"],
            "status": data["status"],
            "status_message": data.get("status_message"),
            "created_at": data["created_at"],
            "last_updated_at": data["last_updated_at"],
            "ttl_ms": data.get("ttl"),
            "poll_interval_ms": data.get("poll_interval"),
        }
        if upstream:
            projected["result"] = upstream.get("result")
            projected["error"] = upstream.get("error")
            projected["input_requests"] = upstream.get("inputRequests") or upstream.get("input_requests")
            # An inlined task outcome is a tool result: it can carry a
            # resource_link, which crosses the front door here and so needs the
            # same upstream-namespacing rewrite as the direct call (#1025).
            # A no-op outside front_door mode.
            identity = get_identity_context()
            tenant_id = identity.caller.tenant_id if identity is not None else None
            project_result_uris(tenant_id, key[0], projected["result"])
        return GetTaskResult(**projected)

    async def _get(ctx: Any, params: Any) -> Any:
        """``tasks/get``: relay to the owning upstream, sync, return the SEP-2663 snapshot.

        SEP-2663 folds the old ``tasks/result`` round trip into this one: a
        completed task's ``CallToolResult`` and a failed task's error arrive
        INLINE on the poll, and an ``input_required`` task carries its
        ``inputRequests`` so the client can answer them via ``tasks/update``.

        An upstream error returns the local snapshot unchanged, with no outcome
        fields -- state is never fabricated. A ``working -> completed``
        transition emits ``TaskCompleted`` exactly once (dedup is atomic inside
        the store).

        The pinned-digest re-verification that used to guard ``tasks/result``
        lives here now, and it MUST: with that method removed, this is the only
        path by which a task's payload reaches a caller. Dropping the check
        along with the method would have quietly retired a supply-chain control
        (ADR-014) while looking like a pure wire change. It runs only when an
        outcome is about to be handed over, matching the old placement.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            _require_tasks_client(ctx, task_id)
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
                    if result.get("result") is not None or result.get("status") == "completed":
                        # Fail-closed supply-chain re-verification; its McpError propagates.
                        # Runs BEFORE the payload is fetched, not just before it is
                        # returned, so a drifted tool is never even asked for output.
                        await asyncio.to_thread(store._verify_pinned_digest, key)
                        result = await _with_upstream_payload(key, task_id, result)
                    return await _flat_snapshot(key, task_id, upstream=result)

            return await _flat_snapshot(key, task_id)
        finally:
            if token is not None:
                identity_context_var.reset(token)

    async def _with_upstream_payload(key: tuple[str, str], task_id: str, upstream: dict[str, Any]) -> dict[str, Any]:
        """Ensure a completed task's payload is present, fetching it if it is not.

        SEP-2663 inlines a completed task's ``result`` on ``tasks/get``, so a
        modern upstream already put it there and this is a no-op.

        An upstream on the older design does not: it answers ``tasks/get`` with a
        status only and keeps the payload behind ``tasks/result``. Hangar no
        longer serves that method downstream -- correctly, SEP-2663 removes it --
        but it must still CALL it upstream, or the payload of every task relayed
        from such a server becomes unreachable: the client polls to
        ``completed`` and gets ``result: null`` forever. Bridging the two
        generations is the relay's job; dropping the downstream method and the
        upstream fetch together is what made this a regression rather than a
        rename.

        Best-effort by design. A modern upstream answers ``-32601`` here, and an
        upstream that simply has nothing to give is not an error either -- in both
        cases the snapshot passes through with no outcome rather than failing a
        poll that otherwise succeeded.
        """
        if upstream.get("result") is not None:
            return upstream

        try:
            payload = await asyncio.to_thread(
                upstream_router, key[0], "tasks/result", {"task_id": task_id}, _RELAY_TIMEOUT
            )
        except Exception:  # noqa: BLE001 -- a missing payload must not fail the poll
            logger.debug("task_payload_fetch_failed", target_server_id=key[0], task_id=task_id)
            return upstream

        if not isinstance(payload, dict) or "error" in payload:
            return upstream
        fetched = payload.get("result")
        if not isinstance(fetched, dict):
            return upstream
        return {**upstream, "result": fetched}

    async def _cancel(ctx: Any, params: Any) -> Any:
        """``tasks/cancel``: best-effort relay, then an EMPTY acknowledgement.

        SEP-2663 makes cancellation cooperative: it may never take effect, and a
        task is allowed to reach a terminal status other than ``cancelled``
        because the work finished first. So the ack carries no status. The
        client polls ``tasks/get`` for what actually happened.

        That is a real change from the SEP-1686 behaviour this replaced, which
        returned a ``CancelTaskResult`` carrying a status -- and on the confirmed
        path OVERWROTE it with ``cancelled`` before returning. Under SEP-2663
        that is precisely the fabrication the spec warns about.

        The ledger still tracks truth: the entry is marked cancelled and retired
        ONLY when the upstream actually confirms, and is otherwise kept with its
        real status intact.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            _require_tasks_client(ctx, task_id)
            key = await _resolve_owned_key(task_id)
            if not await asyncio.to_thread(store.authorize, key):
                raise make_mcp_error(INVALID_PARAMS, f"Task not found: {task_id}")

            resp = await asyncio.to_thread(
                upstream_router, key[0], "tasks/cancel", {"task_id": task_id}, _RELAY_TIMEOUT
            )
            if _cancel_confirmed(resp):
                await asyncio.to_thread(store.mark_cancelled, key)
                await asyncio.to_thread(store.delete_task, key)

            return EmptyResult()
        finally:
            if token is not None:
                identity_context_var.reset(token)

    async def _update(ctx: Any, params: Any) -> Any:
        """``tasks/update`` (2026-07-28 / SEP-2663): the GOVERNED modern input path.

        The client resolves a task's mid-flight ``input_required`` by driving an
        inbound ``tasks/update`` carrying its answers -- so THIS handler is where
        consent is governed. An inbound update IS the client's consent to provide
        input: authorize the tenant, gate the decision on the composite key,
        relay the client's payload upstream verbatim (upstream-truthful), record
        the decision, and re-sync the ledger.

        A transient upstream refusal discards the gate WITHOUT consuming (finding
        #3 -- recoverable) and raises; it does not fail the task.

        Registered unconditionally. It used to be gated on the SDK defining
        ``UpdateTaskRequest``, which it never will -- that type belongs to the
        SEP-2663 extension, not to the SEP-1686 types `mcp_types` kept, so the
        probe was a latch that could not trip and this handler was dead code.

        Returns an EMPTY acknowledgement, per SEP-2663: updates change nothing
        observable by themselves, and answers naming an input request that was
        never issued are ignored rather than rejected. The client polls
        ``tasks/get`` for the resulting state.
        """
        token = _bridge_identity(ctx)
        try:
            task_id = params.task_id
            _require_tasks_client(ctx, task_id)
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
            return EmptyResult()
        finally:
            if token is not None:
                identity_context_var.reset(token)

    # The SEP-2663 method set, byte-exact. `tasks/result` and `tasks/list` are
    # absent BY DESIGN: the SEP removes both, and an unregistered method already
    # yields -32601 from the runner, so their absence here IS the implementation
    # rather than something that still needs handling.
    #
    # No conditional registration. The previous `if HAS_LIST_TASKS` /
    # `if HAS_TASKS_UPDATE` guards read as "track the SDK as it matures", but
    # they watched `mcp_types` -- which carries the frozen SEP-1686 generation,
    # not this extension. `tasks/list` was therefore always served and
    # `tasks/update` never was, permanently and in both cases wrongly (ADR-015).
    low.add_request_handler("tasks/get", _GetTaskParams, _get)
    low.add_request_handler("tasks/cancel", _CancelTaskParams, _cancel)
    low.add_request_handler("tasks/update", _UpdateTaskParams, _update)


def _cancel_confirmed(resp: Any) -> bool:
    """Does a raw upstream response CONFIRM cancellation?

    Confirmed iff it is a clean result (a ``result`` present, no ``error``) whose
    status is either ``cancelled`` or absent. An ``error`` response, a missing
    ``result``, or a result still reporting a non-cancelled status is NOT a
    confirmation, and the entry is then kept with its true status.

    This decides the LEDGER's action only. The client's acknowledgement is empty
    either way, so an unconfirmed cancel is never reported to the caller as a
    successful one -- they learn what happened by polling ``tasks/get``.
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
