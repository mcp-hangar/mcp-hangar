"""The relay seam: govern the upstream task handles a batch captured (ADR-014 D4).

Every path that dispatches through ``BatchExecutor`` and hands its caller the
result runs this once the executor returns: ``hangar_call``, and the front
door's flat ``tools/call`` (#1394). A worker that sees an upstream task handle
only captures it. Nothing reaches the governed task store until this runs, so a
path that skips it hands its caller a handle no ``tasks/*`` method can find.
That is what the flat call did until #1394.

An upstream answers with a task in one of two shapes, and :func:`upstream_task`
reads both, so they are captured and governed alike (#1405). A task goes only to
a caller that can poll it. Any other caller is refused here, before anything is
recorded, because ``tasks/*`` would refuse it the task anyway -- and the upstream
is asked, best effort, to cancel the task nobody is being handed (#1492).

It lives apart from ``hangar_call`` so the flat call can reach it. The flat
projection imports it lazily, for the import cycle the batch package is in (#894).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ....application.tasks.tool_pin_context import reset_current_tool_pin, set_current_tool_pin
from ....context import caller_polls_tasks_var, identity_context_var
from ....domain.services.task_ownership import TaskOwner
from ....logging_config import get_logger
from ....tasks_wire import EXTENSION_ID
from ...context import get_context
from .models import CallResult, RelayCapture

logger = get_logger(__name__)

#: SEP-2663's names for the two task fields the ledger stores under SEP-1686's.
_FLAT_TO_LEDGER = {"ttlMs": "ttl", "pollIntervalMs": "pollInterval"}

#: What a caller that cannot poll a task is told instead of being handed one.
_CANNOT_POLL = (
    "Upstream answered with a task, and this caller cannot poll one: it did not "
    f"declare the {EXTENSION_ID} extension, or its protocol revision has no tasks/*. "
    "The task was not handed over."
)

#: How long the best-effort ``tasks/cancel`` for an unhanded task may take.
#: One attempt, then give up: the caller's refusal does not wait on it and
#: nothing downstream depends on the answer.
_CANCEL_TIMEOUT = 10.0


def _unhanded_task_id(upstream: Any) -> str | None:
    """The id of a task no caller is being handed, or ``None`` if it has none.

    Read through :func:`upstream_task`, so it is the id the store would have
    been keyed on had the task been governed. An unreadable handle yields
    ``None`` and nothing is sent: an id Hangar cannot name is an id no
    ``tasks/cancel`` could carry.
    """
    task = upstream_task(upstream) or {}
    for key in ("taskId", "task_id", "id"):
        value = task.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _cancel_unhanded_task(capture: RelayCapture) -> None:
    """Ask the upstream to cancel a task no caller is handed. Best effort (#1492).

    The upstream has already created the task and the seam is about to refuse
    it, so nothing will ever poll it: left alone it runs until its own TTL, and
    the work is being done for nobody. SEP-2663 makes cancellation cooperative,
    so this is a request and not a guarantee -- which is why the caller's
    refusal neither waits for it nor changes with its outcome.

    **Off the request path.** ``relay_request`` is a blocking network call and
    this seam runs on the loop serving every other request on the connection, so
    the cancel goes on one short-lived daemon thread with a bounded timeout.
    One attempt, no retry, no backlog: a failure is logged and dropped.

    **Logged by outcome type only.** ``cancelled``, ``refused`` (the upstream
    answered an error, of which only the JSON-RPC code is recorded) or
    ``failed`` (the relay itself raised, of which only the exception class is).
    No upstream text is logged, here or anywhere on this seam.

    Silent no-ops, both fail-safe: no router on the application context (the
    relay is not wired, so there is nothing to relay through), and a handle
    carrying no readable task id.
    """
    task_id = _unhanded_task_id(capture.upstream)
    if task_id is None:
        return
    try:
        router = getattr(get_context(), "task_upstream_router", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local): nothing to cancel through
        router = None
    if router is None:
        return

    target_server_id = capture.target_server_id
    mcp_server = capture.logical_mcp_server
    tool = capture.tool

    def _cancel() -> None:
        try:
            # The param shape the served `tasks/cancel` relays upstream, so an
            # upstream sees one kind of cancel whoever asked for it.
            response = router(target_server_id, "tasks/cancel", {"task_id": task_id}, _CANCEL_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 -- fault barrier: a cancel must never surface anywhere
            logger.info(
                "task_relay_cancel_unhanded_task",
                outcome="failed",
                error_type=type(exc).__name__,
                mcp_server=mcp_server,
                tool=tool,
                task_id=task_id,
            )
            return
        error = response.get("error") if isinstance(response, dict) else None
        logger.info(
            "task_relay_cancel_unhanded_task",
            outcome="refused" if error else "cancelled",
            error_code=error.get("code") if isinstance(error, dict) else None,
            mcp_server=mcp_server,
            tool=tool,
            task_id=task_id,
        )

    threading.Thread(target=_cancel, name="hangar-task-cancel", daemon=True).start()


def upstream_task(result: Any) -> dict[str, Any] | None:
    """The task an upstream ``tools/call`` answered with, or ``None`` for any other result.

    An upstream answers in one of two shapes, and both are the same task:

    * the current flat ``CreateTaskResult`` (SEP-2663, 2026-07-28): the task's
      fields at the top level, marked ``resultType: "task"``;
    * the older nested one (SEP-1686, 2025-11-25): the task under a ``task``
      key.

    Returned in the field names the ledger stores, which are the older ones:
    ``ttlMs`` and ``pollIntervalMs`` become ``ttl`` and ``pollInterval``. The
    worker's capture, the seam's registration and the flat call's task result
    all read a task through this, so the three agree on what one is. A malformed
    task is still returned; registering it fails closed.
    """
    if not isinstance(result, dict):
        return None
    if result.get("resultType") == "task":
        task = {key: value for key, value in result.items() if key not in ("resultType", "_meta")}
        for flat, ledger in _FLAT_TO_LEDGER.items():
            if flat in task:
                task.setdefault(ledger, task.pop(flat))
        return task
    nested = result.get("task")
    if isinstance(nested, dict) and any(key in nested for key in ("taskId", "task_id", "id", "status")):
        return nested
    return None


def govern_relayed_tasks(executed: list[CallResult]) -> None:
    """P3.3 relay seam: govern captured upstream task handles ON THE MAIN LOOP.

    ADR-014 D4 binds governance on the request path, never in a worker thread.
    Each batch worker that saw an upstream task handle (kill-switch on) attached a
    :class:`RelayCapture` to its (success) CallResult but performed NO store write.
    Here, back on the main loop and BEFORE the response reaches the caller, we
    run the atomic ``store.relay_and_govern`` (register +
    ``TaskCreated`` emit) for each such result, rewriting ``executed`` in place.

    Outcomes per captured result:
      - store absent (kill-switch off / no app ctx) -> safety: rewrite to the
        TaskRelayNotSupported rejection (never hand back an ungoverned handle).
      - the caller cannot poll a task (``caller_polls_tasks_var``, #1405) ->
        rewrite to a ``TasksNotNegotiated`` refusal. Nothing is recorded: no
        ``tasks/*`` call of this caller's could ever reach the task. The upstream
        is asked to cancel it, best effort and off the request path (#1492), so
        work nobody can collect does not run on to its TTL.
      - mint/register/emit fails -> rewrite to a DISTINCT
        ``TaskRelayRegistrationFailed`` failure; ``relay_and_govern``'s atomic
        rollback guarantees zero governed state survives.
      - success -> a pre-built success CallResult carrying the raw, now-governed
        upstream handle.

    The captured identity (and digest pin) are re-bound into their contextvars for
    the duration so ``relay_and_govern``'s owner cross-check and digest pin see the
    same request context the worker authorized -- not a live/foreign contextvar.
    """
    try:
        store = getattr(get_context(), "governed_task_store", None)
    except Exception:  # noqa: BLE001 -- no app context (stdio/local): treat as kill-switch off
        store = None
    # Read on the request path, where the task relay's middleware bound it.
    caller_polls = caller_polls_tasks_var.get()

    for i, r in enumerate(executed):
        capture = r.relay_capture
        if capture is None:
            continue

        if store is None:
            # Safety net: a capture with no store to govern it must never reach the
            # client as a live handle. Fall back to the relay-only rejection.
            executed[i] = CallResult(
                index=r.index,
                call_id=r.call_id,
                success=False,
                error=(
                    "Upstream returned an MCP task handle; Hangar does not yet relay "
                    "or govern task results (relay-only, ADR-008). The task is not "
                    "tracked, so the handle is unusable."
                ),
                error_type="TaskRelayNotSupported",
                elapsed_ms=r.elapsed_ms,
            )
            continue

        if not caller_polls:
            logger.info(
                "task_relay_refused_caller_cannot_poll",
                call_id=r.call_id,
                mcp_server=capture.logical_mcp_server,
                tool=capture.tool,
            )
            _cancel_unhanded_task(capture)
            executed[i] = CallResult(
                index=r.index,
                call_id=r.call_id,
                success=False,
                error=_CANNOT_POLL,
                error_type="TasksNotNegotiated",
                elapsed_ms=r.elapsed_ms,
            )
            continue

        seam_start = time.perf_counter()
        # Re-bind the CAPTURED request context (identity + digest pin) for the
        # duration of the governed relay, then always restore it.
        _id_token = identity_context_var.set(capture.identity)
        _pin_token = set_current_tool_pin(capture.pin) if capture.pin is not None else None
        try:
            try:
                # capture.upstream is the raw upstream task result, in either
                # shape -- byte-identical to what the client will receive.
                # ``mint_from_upstream`` mints from the task object alone, in the
                # ledger's field names, which is what ``upstream_task`` returns
                # (a malformed one fails closed via mint's ValueError).
                task = store.mint_from_upstream(upstream_task(capture.upstream) or {})
            except ValueError as exc:
                # Fail-closed extraction: a malformed/idless upstream task handle.
                # TODO(P3.4): increment a relay-registration-failure metric counter.
                logger.warning(
                    "task_relay_mint_failed",
                    call_id=r.call_id,
                    mcp_server=capture.logical_mcp_server,
                    tool=capture.tool,
                    error=str(exc),
                )
                executed[i] = CallResult(
                    index=r.index,
                    call_id=r.call_id,
                    success=False,
                    error=f"Failed to register relayed task: {exc}",
                    error_type="TaskRelayRegistrationFailed",
                    elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
                )
                continue

            # Pre-build the final success result now (mint done), so once the
            # atomic register+publish below succeeds nothing fallible remains --
            # elapsed honestly includes the mint/register/emit cost.
            success_result = CallResult(
                index=r.index,
                call_id=r.call_id,
                success=True,
                result=capture.upstream,
                elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
            )

            if capture.identity is not None and capture.identity.caller is not None:
                _caller = capture.identity.caller
                expected_owner = TaskOwner(
                    tenant_id=_caller.tenant_id,
                    principal_id=_caller.user_id or _caller.agent_id,
                )
            else:
                expected_owner = TaskOwner(tenant_id=None, principal_id=None)

            try:
                store.relay_and_govern(
                    target_server_id=capture.target_server_id,
                    task=task,
                    expected_owner=expected_owner,
                    correlation_id=capture.correlation_id,
                    mcp_server_id=capture.logical_mcp_server,
                    tool_name=capture.tool,
                )
            except Exception as exc:  # noqa: BLE001 -- any register/emit failure -> distinct fail-closed result
                # relay_and_govern's atomic rollback leaves ZERO governed state.
                # TODO(P3.4): increment a relay-registration-failure metric counter.
                logger.warning(
                    "task_relay_registration_failed",
                    call_id=r.call_id,
                    mcp_server=capture.logical_mcp_server,
                    tool=capture.tool,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                executed[i] = CallResult(
                    index=r.index,
                    call_id=r.call_id,
                    success=False,
                    error=f"Failed to register relayed task: {exc}",
                    error_type="TaskRelayRegistrationFailed",
                    elapsed_ms=r.elapsed_ms + (time.perf_counter() - seam_start) * 1000,
                )
                continue

            # Governed: hand the client the raw, now-tracked upstream handle.
            executed[i] = success_result
        finally:
            if _pin_token is not None:
                reset_current_tool_pin(_pin_token)
            identity_context_var.reset(_id_token)
