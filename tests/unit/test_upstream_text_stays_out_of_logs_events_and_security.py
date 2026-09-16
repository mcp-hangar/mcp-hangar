"""An upstream's text reaches no log, event or security record (#1472).

Following #1454, three more paths carried what an upstream wrote:

- the line logged when an upstream refuses ``initialize`` repeated the stderr
  its process had printed;
- ``McpServerDegraded.reason`` held a failed start's error text, and the event
  reaches the event store, the audit log and every event handler;
- ``tool_error_hook`` sent ``<type>: <message>`` to the security handler.

Each test puts a sentinel in the upstream's text and asserts it reaches none of
them, while the bounded value that replaces it does.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from structlog.testing import capture_logs

from mcp_hangar.application.sagas.mcp_server_recovery_saga import McpServerRecoverySaga
from mcp_hangar.domain.events import McpServerDegraded
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.server.validation import tool_error_hook

SENTINEL = "SENTINEL-UPSTREAM-TEXT"
GET_LAUNCHER = "mcp_hangar.infrastructure.launchers.get_launcher"


def _no_sentinel(logs: list[dict[str, Any]]) -> None:
    assert all(SENTINEL not in repr(entry) for entry in logs), logs


class TestTheInitializeRefusal:
    def test_the_log_carries_the_exit_code_and_the_stderr_size_not_the_stderr(self) -> None:
        stderr = f"Traceback (most recent call last):\n  {SENTINEL}\n"
        client = MagicMock()
        client.process.poll.return_value = 1
        client._last_stderr = stderr
        client.call.return_value = {"error": {"code": -32603, "message": f"refused: {SENTINEL}"}}
        server = McpServer(mcp_server_id="math", mode="subprocess", command=["unused"])

        with capture_logs() as logs, pytest.raises(McpServerStartError) as raised:
            server._perform_mcp_handshake(client)

        _no_sentinel(logs)
        assert [entry for entry in logs if entry["event"] == "mcp_server_initialize_refused"] == [
            {
                "event": "mcp_server_initialize_refused",
                "log_level": "error",
                "mcp_server_id": "math",
                "exit_code": 1,
                "stderr_bytes": len(stderr.encode()),
            }
        ]
        assert SENTINEL in str(raised.value), "the caller still gets the upstream's error"


class TestTheDegradedEvent:
    def test_a_failed_start_records_its_error_type_as_the_reason(self) -> None:
        client = MagicMock()
        client.process = None
        client.call.side_effect = ConnectionError(SENTINEL)
        launcher = MagicMock()
        launcher.launch.return_value = client
        server = McpServer(mcp_server_id="math", mode="subprocess", command=["unused"], max_consecutive_failures=1)

        with capture_logs() as logs, patch(GET_LAUNCHER, return_value=launcher):
            with pytest.raises(McpServerStartError):
                server.ensure_ready()

        degraded = [event for event in server.collect_events() if isinstance(event, McpServerDegraded)]
        assert len(degraded) == 1
        assert degraded[0].reason == "ConnectionError"
        assert SENTINEL not in repr(degraded[0].to_dict())
        _no_sentinel(logs)

    def test_the_recovery_saga_still_restarts_a_server_degraded_by_a_start(self) -> None:
        # It skips a `capability_violation:` reason; an error type is never one.
        saga_manager = MagicMock()
        saga = McpServerRecoverySaga(saga_manager=saga_manager)

        assert saga.handle(McpServerDegraded("math", 1, 1, "ConnectionError")) == []
        saga_manager.schedule_command.assert_called_once()


class TestTheSecurityHandler:
    def test_a_failed_tool_call_sends_its_error_type_only(self) -> None:
        security_handler = MagicMock()
        context = SimpleNamespace(security_handler=security_handler)

        with patch("mcp_hangar.server.validation.get_context", return_value=context):
            tool_error_hook(RuntimeError(SENTINEL), {"mcp_server_id": "math"})

        security_handler.log_validation_failed.assert_called_once()
        call = security_handler.log_validation_failed.call_args
        assert call.kwargs["message"] == "RuntimeError"
        assert SENTINEL not in repr(call)
