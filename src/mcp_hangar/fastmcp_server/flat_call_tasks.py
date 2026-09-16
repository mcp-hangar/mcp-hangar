"""A task an upstream creates in answer to a front door's flat ``tools/call`` (#1394).

An upstream may answer ``tools/call`` with a task handle rather than a tool
result. ``hangar_call`` records who owns that task in the governed task store
before the handle reaches its caller, and ``tasks/get``, ``tasks/cancel`` and
``tasks/update`` answer only that owner. The flat call path dispatched through
the same executor and skipped the recording, so every ``tasks/*`` on such a task
answered "Task not found", its creator included. On the shipped SDK the caller
never saw the handle either: the upstream's nested ``{"task": {...}}`` is not a
``CallToolResult``, so the call failed validation with ``-32602``.

This module fixes both, outside the handler, which is at its complexity ceiling:

* :func:`govern_flat_call` runs the seam ``hangar_call`` runs,
  :func:`~mcp_hangar.server.tools.batch.relay_seam.govern_relayed_tasks`. The
  task is owned by the same tenant and principal, and a task the seam cannot
  govern becomes the same failure.
* The governed task goes back as the SEP-2663 ``CreateTaskResult``, the flat
  ``resultType: "task"`` shape a 2026-07-28 client reads a task from. Its fields
  are the ones ``tasks/get`` serves later, renamed by the same function.

Only a task the seam governed becomes a task result. The decision is made on the
worker's capture, never on the shape of what the upstream sent, so an upstream
cannot hand the caller a task result the store has not recorded.

The upstream's task may come in the current flat shape or the older nested one;
both are captured, governed and answered alike (#1405). A caller that cannot poll
a task is never handed one: the seam refuses the call instead, and the caller
gets a tool error naming the extension to declare.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..application.tasks.governed_task_store import GovernedTaskStore
from ..tasks_wire import CreateTaskResult
from .task_relay_handlers import task_wire_fields

if TYPE_CHECKING:
    from ..server.tools.batch.models import CallResult


def govern_flat_call(results: list[CallResult]) -> tuple[CallResult, CreateTaskResult | None]:
    """Govern a flat call's result, as ``hangar_call`` governs its results.

    Args:
        results: The executor's results. The flat path sends one call.

    Returns:
        The call's result after governance, and the task result to answer its
        caller with when the upstream created a task the store now governs.
        ``None`` for every other result. That includes a task the seam could not
        govern, or would not hand this caller, which the seam has turned into a
        failed result.
    """
    # Lazily: the batch package reaches `server.bootstrap`, which imports the
    # flat projection back (#894).
    from ..server.tools.batch.relay_seam import govern_relayed_tasks

    capture = results[0].relay_capture
    govern_relayed_tasks(results)
    result = results[0]
    if capture is None or not result.success:
        return result, None
    return result, _created_task(capture.upstream)


def _created_task(upstream: dict[str, Any]) -> CreateTaskResult:
    """The SEP-2663 task result for a governed upstream handle, in either shape.

    Parsed as the seam parsed it when registering the task, so the caller gets
    the task id the store is keyed on.
    """
    from ..server.tools.batch.relay_seam import upstream_task

    snapshot = GovernedTaskStore.mint_from_upstream(upstream_task(upstream) or {})
    return CreateTaskResult(**task_wire_fields(snapshot))
