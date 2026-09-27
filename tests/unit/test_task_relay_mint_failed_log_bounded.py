"""The ``task_relay_mint_failed`` warning carries the bounded error type, never the handle (#1607).

When an upstream returns a malformed task handle, ``mint_from_upstream`` raises
pydantic's ``ValidationError``, whose text echoes the handle as ``input_value=...``.
That warning logged ``str(exc)``, so the upstream's own text reached WARNING. The
data-handling contract allows event type, identifiers and bounded codes at INFO
and above, with full detail at DEBUG (#1276, R7).

The caller is still told, in the tool result. Only the log changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import structlog

from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.context import caller_polls_tasks_var, identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.server.tools.batch.models import CallResult, RelayCapture
from mcp_hangar.server.tools.batch.relay_seam import govern_relayed_tasks

#: Planted in the upstream's malformed task handle.
CANARY = "canary-1607-upstream-handle-text"


def _identity() -> IdentityContext:
    return IdentityContext(
        caller=CallerIdentity(
            user_id="alice",
            agent_id=None,
            session_id=None,
            principal_type="user",
            tenant_id="tenant-a",
        )
    )


def _govern_malformed_handle() -> tuple[CallResult, list[dict[str, Any]]]:
    # A task id, so mint gets past the id check, but a status that is no task
    # status: pydantic rejects it and echoes the value in its message.
    upstream = {
        "task": {
            "taskId": "t1",
            "status": CANARY,
            "createdAt": "2020-01-01T00:00:00Z",
            "lastUpdatedAt": "2020-01-01T00:00:00Z",
            "ttl": 60_000,
        }
    }
    identity = _identity()
    capture = RelayCapture(
        identity=identity,
        pin=None,
        target_server_id="server_a",
        correlation_id="call-1",
        upstream=upstream,
        logical_mcp_server="server_a",
        tool="long_running_op",
    )
    executed = [
        CallResult(index=0, call_id="call-1", success=True, result=upstream, elapsed_ms=1.0, relay_capture=capture)
    ]
    ctx = SimpleNamespace(governed_task_store=GovernedTaskStore(event_publisher=lambda _e: None))
    token = identity_context_var.set(identity)
    polls_token = caller_polls_tasks_var.set(True)
    try:
        with (
            patch("mcp_hangar.server.tools.batch.relay_seam.get_context", return_value=ctx),
            structlog.testing.capture_logs() as captured,
        ):
            govern_relayed_tasks(executed)
    finally:
        caller_polls_tasks_var.reset(polls_token)
        identity_context_var.reset(token)
    return executed[0], captured


def test_the_warning_carries_the_bounded_type_and_no_upstream_text() -> None:
    result, captured = _govern_malformed_handle()

    [warning] = [entry for entry in captured if entry["event"] == "task_relay_mint_failed"]
    assert warning["log_level"] == "warning"
    at_info_or_above = [entry for entry in captured if entry["log_level"] != "debug"]
    assert all(CANARY not in str(entry) for entry in at_info_or_above), at_info_or_above
    assert warning["error_type"] == "ValidationError"
    assert (warning["call_id"], warning["mcp_server"], warning["tool"]) == ("call-1", "server_a", "long_running_op")

    # The caller-facing result is unchanged: it still names what was wrong.
    assert result.success is False
    assert result.error_type == "TaskRelayRegistrationFailed"
    assert result.error is not None and CANARY in result.error


def test_the_full_text_is_kept_at_debug() -> None:
    _, captured = _govern_malformed_handle()

    detail = [entry for entry in captured if entry["log_level"] == "debug" and CANARY in str(entry)]
    assert [entry["event"] for entry in detail] == ["task_relay_mint_failed_detail"]
