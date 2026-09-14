"""A failed start closes the client it launched, on every path, after its waiters.

What ``McpServer._start`` guarantees about a client an attempt launched and did
not keep:

- it is closed from a ``finally``, so a failure raised while handling the start
  failure still closes it;
- the connection gauge is reset before the waiters are woken, and nothing after
  them touches it, so a start one of them begins is never read as disconnected;
- a ``close()`` that raises is logged by its exception type, never by its text,
  which can carry what the upstream wrote, and does not replace the start error;
- a start that succeeds keeps its client open.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.contracts.metrics_publisher import IMetricsPublisher
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.mcp_server import McpServer

pytestmark = pytest.mark.security

GET_LAUNCHER = "mcp_hangar.infrastructure.launchers.get_launcher"
LOGGER = "mcp_hangar.domain.model.mcp_server.logger"


def _launcher(client: Any) -> MagicMock:
    launcher = MagicMock()
    launcher.launch.return_value = client
    return launcher


def _refusing_client() -> MagicMock:
    """A client whose upstream refuses ``initialize``, with no process to diagnose."""
    client = MagicMock()
    client.process = None
    client.call.return_value = {"error": {"code": -32603, "message": "handshake refused"}}
    return client


def _answering_client() -> MagicMock:
    """A client whose upstream completes the handshake."""
    client = MagicMock()
    client.process = None

    def _call(method: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-06-18"}}
        return {"result": {"tools": []}}

    client.call.side_effect = _call
    return client


def _server(**kwargs: Any) -> McpServer:
    return McpServer(mcp_server_id="fake", mode="subprocess", command=["unused"], **kwargs)


class TestAFailedStart:
    def test_closes_its_client_when_handling_the_failure_raises(self) -> None:
        client = _refusing_client()
        server = _server()

        with (
            patch(GET_LAUNCHER, return_value=_launcher(client)),
            patch.object(server, "_handle_start_failure", side_effect=RuntimeError("handling failed")),
            pytest.raises(RuntimeError, match="handling failed"),
        ):
            server.ensure_ready()

        client.close.assert_called_once_with()

    def test_resets_the_gauge_before_waking_its_waiters_and_never_after(self) -> None:
        publisher = Mock(spec=IMetricsPublisher)
        client = _refusing_client()
        server = _server(metrics_publisher=publisher)
        seen: list[str] = []

        def _when() -> str:
            return "after waking" if server._ready_event.is_set() else "before waking"

        publisher.set_connection_active.side_effect = lambda _sid, active: seen.append(f"gauge {active} {_when()}")
        client.close.side_effect = lambda: seen.append(f"close {_when()}")

        with patch(GET_LAUNCHER, return_value=_launcher(client)), pytest.raises(McpServerStartError):
            server.ensure_ready()

        assert seen == ["gauge True before waking", "gauge False before waking", "close after waking"]

    def test_logs_a_close_that_raises_by_its_type_not_its_text(self) -> None:
        client = _refusing_client()
        client.close.side_effect = RuntimeError("text the upstream wrote")
        server = _server()

        with (
            patch(GET_LAUNCHER, return_value=_launcher(client)),
            patch(LOGGER) as log,
            pytest.raises(McpServerStartError, match="handshake refused"),
        ):
            server.ensure_ready()

        (warning,) = [c for c in log.warning.call_args_list if c.args == ("failed_start_client_close_error",)]
        assert warning.kwargs == {"mcp_server_id": "fake", "error_type": "RuntimeError"}
        assert "text the upstream wrote" not in repr(log.mock_calls)


class TestAStartThatSucceeds:
    def test_keeps_its_client_open(self) -> None:
        client = _answering_client()
        server = _server()

        with patch(GET_LAUNCHER, return_value=_launcher(client)):
            server.ensure_ready()

        client.close.assert_not_called()
        assert server._client is client
