"""The relay seam: govern the upstream task handles a batch captured (ADR-014 D4).

Every path that dispatches through ``BatchExecutor`` and hands its caller the
result runs this once the executor returns: ``hangar_call``, and the front
door's flat ``tools/call`` (#1394). A worker that sees an upstream task handle
only captures it. Nothing reaches the governed task store until this runs, so a
path that skips it hands its caller a handle no ``tasks/*`` method can find.
That is what the flat call did until #1394.

It lives apart from ``hangar_call`` so the flat call can reach it. The flat
projection imports it lazily, for the import cycle the batch package is in (#894).
"""

from __future__ import annotations

import time

from ....application.tasks.tool_pin_context import reset_current_tool_pin, set_current_tool_pin
from ....context import identity_context_var
from ....domain.services.task_ownership import TaskOwner
from ....logging_config import get_logger
from ...context import get_context
from .models import CallResult

logger = get_logger(__name__)


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

        seam_start = time.perf_counter()
        # Re-bind the CAPTURED request context (identity + digest pin) for the
        # duration of the governed relay, then always restore it.
        _id_token = identity_context_var.set(capture.identity)
        _pin_token = set_current_tool_pin(capture.pin) if capture.pin is not None else None
        try:
            try:
                # capture.upstream is the raw upstream ``CreateTaskResult``
                # (``{"task": {...}}``) -- byte-identical to what the client will
                # receive. ``mint_from_upstream`` mints from the FLAT task object,
                # so unwrap the ``task`` member here (a non-dict / missing member
                # is malformed -> fail closed via mint's ValueError).
                _task_obj = capture.upstream.get("task") if isinstance(capture.upstream, dict) else None
                task = store.mint_from_upstream(_task_obj if isinstance(_task_obj, dict) else {})
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
