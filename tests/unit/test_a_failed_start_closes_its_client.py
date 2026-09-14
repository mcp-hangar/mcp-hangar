"""A start closes every client it does not keep.

A failed start never closed the client it had just created: for a stdio
upstream a child process and its pipes, for a remote one an HTTP client. A
start that succeeded replaced the client the server already held without
closing it. Each leaked once per attempt, for the life of the gateway.

Pinned here, each through the aggregate's real start path:

- failed starts against a real stdio upstream leave none of its processes
  running, and every client they made closed;
- a restart from DEGRADED closes the client it replaces, and a failed one
  closes both the client the server held and its own;
- a failed start that leaves the server DEAD closes its client, and a call
  that revives a DEAD server closes every client but the one it keeps;
- a health check or a call that finds the upstream's process gone closes what
  is left of its connection;
- a failed remote start closes its HTTP client;
- closing is safe: a ``close()`` that raises does not replace the start error,
  a failure collecting diagnostics still closes the client and fails the start,
  and a client launched but not yet handed back is closed too.

The failed-start close is ``McpServer._discard_failed_client``, which ``_start``
calls from its ``finally``.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.domain.contracts.metrics_publisher import IMetricsPublisher
from mcp_hangar.domain.exceptions import McpServerStartError
from mcp_hangar.domain.model.mcp_server import DEAD_START_FAILED, McpServer
from mcp_hangar.domain.value_objects import McpServerState

pytestmark = pytest.mark.security

REFUSING_UPSTREAM = Path(__file__).with_name("_handshake_refusing_upstream.py")
MOCK_PROVIDER = Path(__file__).resolve().parents[1] / "mock_provider.py"
FAILED_STARTS = 3
GET_LAUNCHER = "mcp_hangar.infrastructure.launchers.get_launcher"


def _recording(server: McpServer) -> list[Any]:
    """Every client ``server`` creates from now on, in order."""
    created: list[Any] = []
    create = server._create_client

    def _create() -> Any:
        client = create()
        created.append(client)
        return client

    server._create_client = _create  # type: ignore[method-assign]
    return created


def _reap(clients: list[Any]) -> None:
    """Kill any process a failing run left behind, so it does not outlive the test."""
    for client in clients:
        process = getattr(client, "process", None)
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def _unused_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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


class TestAFailedStdioStart:
    def test_leaves_no_upstream_process_running(self) -> None:
        server = McpServer(
            mcp_server_id="refuses-handshake",
            mode="subprocess",
            command=[sys.executable, str(REFUSING_UPSTREAM)],
            # High enough that every failure reads DEAD, which a start may retry
            # at once, rather than DEGRADED, which waits out a backoff first.
            max_consecutive_failures=FAILED_STARTS + 1,
        )
        created = _recording(server)
        try:
            for _ in range(FAILED_STARTS):
                with pytest.raises(McpServerStartError, match="handshake refused"):
                    server.ensure_ready()

            assert len(created) == FAILED_STARTS
            running = [client.process.pid for client in created if client.process.poll() is None]
            assert running == [], f"upstream processes still running after {FAILED_STARTS} failed starts: {running}"
            assert [client.closed for client in created] == [True] * FAILED_STARTS
        finally:
            _reap(created)


class TestARestart:
    def test_closes_the_client_it_replaces(self) -> None:
        server = McpServer(
            mcp_server_id="degrades-then-restarts",
            mode="subprocess",
            command=[sys.executable, str(MOCK_PROVIDER)],
            max_consecutive_failures=1,
        )
        created = _recording(server)
        try:
            server.ensure_ready()
            first = created[0]

            # A health check that times out degrades the server, and a degrade
            # leaves its connection open: that is the client a restart replaces.
            with patch.object(first, "call", side_effect=TimeoutError("health check timed out")):
                assert server.health_check() is False
            assert server.state is McpServerState.DEGRADED
            assert first.process.poll() is None

            # The backoff is the recovery saga's to wait out, not this test's.
            with patch.object(server.health, "can_retry", return_value=True):
                server.ensure_ready()

            assert len(created) == 2
            second = created[1]
            assert first.closed is True, "the client the restart replaced was left open"
            assert first.process.poll() is not None, "the replaced client's process is still running"
            assert server._client is second
            assert second.closed is False
            assert second.process.poll() is None
        finally:
            server.shutdown()
            _reap(created)

    def test_a_failed_restart_closes_the_held_client_and_its_own(self) -> None:
        held = MagicMock()
        attempt = _refusing_client()
        server = _server()
        server._client = held
        server._state = McpServerState.DEGRADED

        with patch(GET_LAUNCHER, return_value=_launcher(attempt)):
            with pytest.raises(McpServerStartError, match="handshake refused"):
                server.ensure_ready()

        held.close.assert_called_once()
        attempt.close.assert_called_once()
        assert server._client is None

    def test_a_successful_restart_closes_the_held_client_and_keeps_its_own(self) -> None:
        held = MagicMock()
        attempt = _answering_client()
        server = _server()
        server._client = held
        server._state = McpServerState.DEGRADED

        with patch(GET_LAUNCHER, return_value=_launcher(attempt)):
            server.ensure_ready()

        held.close.assert_called_once()
        attempt.close.assert_not_called()
        assert server._client is attempt


class TestACrashedUpstream:
    def test_the_health_check_that_finds_it_closes_its_client(self) -> None:
        server = McpServer(
            mcp_server_id="crashes",
            mode="subprocess",
            command=[sys.executable, str(MOCK_PROVIDER)],
        )
        created = _recording(server)
        try:
            server.ensure_ready()
            client = created[0]
            client.process.kill()
            client.process.wait(timeout=5)

            assert server.health_check() is False
            assert server.state is McpServerState.DEAD
            assert client.closed is True, "the crashed upstream's client was kept open"
            assert server._client is None
        finally:
            server.shutdown()
            _reap(created)

    def test_a_call_that_finds_it_closes_its_client_before_restarting(self) -> None:
        dead = MagicMock()
        dead.is_alive.return_value = False
        attempt = _answering_client()
        server = _server()
        server._client = dead
        server._state = McpServerState.READY

        with patch(GET_LAUNCHER, return_value=_launcher(attempt)):
            server.ensure_ready()

        dead.close.assert_called_once()
        assert server._client is attempt


class TestAFailedRemoteStart:
    def test_closes_its_http_client(self) -> None:
        server = McpServer(
            mcp_server_id="unreachable-remote",
            mode="remote",
            endpoint=f"http://127.0.0.1:{_unused_port()}/mcp",
            http={"max_retries": 0, "connect_timeout": 2.0},
        )
        created = _recording(server)

        with pytest.raises(McpServerStartError):
            server.ensure_ready()

        assert len(created) == 1
        client = created[0]
        assert client.is_alive() is False, "the failed start's HTTP client was left open"
        assert client._client.is_closed


class TestClosingIsSafe:
    def test_a_close_that_raises_does_not_replace_the_start_error(self) -> None:
        client = _refusing_client()
        client.close.side_effect = RuntimeError("close failed")

        with patch(GET_LAUNCHER, return_value=_launcher(client)):
            with pytest.raises(McpServerStartError, match="handshake refused"):
                _server().ensure_ready()

        client.close.assert_called_once()

    def test_a_failure_collecting_diagnostics_still_closes_the_client_and_fails_the_start(self) -> None:
        client = MagicMock()
        client.process = None
        client.call.side_effect = OSError("pipe closed")
        server = _server()

        with (
            patch(GET_LAUNCHER, return_value=_launcher(client)),
            patch.object(McpServer, "_collect_startup_diagnostics", side_effect=RuntimeError("diagnostics failed")),
        ):
            with pytest.raises(McpServerStartError, match="pipe closed"):
                server.ensure_ready()

        client.close.assert_called_once()
        # The failure was recorded and every waiter woken: not left INITIALIZING.
        assert server.state is McpServerState.DEAD
        assert server._ready_event.is_set()

    def test_a_client_launched_but_not_handed_back_is_closed(self) -> None:
        client = _refusing_client()
        server = _server(log_buffer=MagicMock())

        with (
            patch(GET_LAUNCHER, return_value=_launcher(client)),
            patch.object(McpServer, "_start_stderr_reader", side_effect=RuntimeError("can't start new thread")),
        ):
            with pytest.raises(McpServerStartError, match="can't start new thread"):
                server.ensure_ready()

        client.close.assert_called_once()
        client.call.assert_not_called()

    def test_a_stop_during_the_handshake_leaves_the_attempts_client_closed(self) -> None:
        # The finalize that the stop makes fail has already handed the client
        # over, so the failure handling and `_start` may both close it: safe,
        # since `close()` is idempotent by contract. What matters is that it is
        # closed and that the stopped server does not keep it.
        server = _server()
        client = _answering_client()
        answer = client.call.side_effect

        def _call(method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            if method == "tools/list":
                server.shutdown()  # an operator's stop lands mid-handshake
            return answer(method, params, **kwargs)

        client.call.side_effect = _call

        with patch(GET_LAUNCHER, return_value=_launcher(client)):
            with pytest.raises(McpServerStartError):
                server.ensure_ready()

        assert client.close.called
        assert server._client is None

    def test_a_failed_start_reports_no_connection(self) -> None:
        publisher = Mock(spec=IMetricsPublisher)
        server = _server(metrics_publisher=publisher)

        with patch(GET_LAUNCHER, return_value=_launcher(_refusing_client())):
            with pytest.raises(McpServerStartError):
                server.ensure_ready()

        publisher.set_connection_active.assert_called_with(server.mcp_server_id, False)


def _serving_client() -> MagicMock:
    """A client whose upstream completes the handshake and serves ``add``."""
    client = MagicMock()
    client.process = None
    client.is_alive.return_value = True

    def _call(method: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        if method == "initialize":
            return {"result": {"protocolVersion": "2025-06-18"}}
        if method == "tools/list":
            return {"result": {"tools": [{"name": "add", "description": "", "inputSchema": {"type": "object"}}]}}
        return {"result": {"content": [{"type": "text", "text": "3"}]}}

    client.call.side_effect = _call
    return client


class TestADeadServer:
    """A failed start that leaves the server DEAD, and a call that revives it."""

    def test_a_failed_start_that_leaves_it_dead_closes_its_client(self) -> None:
        client = _refusing_client()
        server = _server(max_consecutive_failures=3)

        with patch(GET_LAUNCHER, return_value=_launcher(client)):
            with pytest.raises(McpServerStartError, match="handshake refused"):
                server.ensure_ready()

        assert server.state is McpServerState.DEAD
        assert server.dead_reason_snapshot == DEAD_START_FAILED
        client.close.assert_called_once_with()

    def test_a_call_that_revives_it_closes_every_client_but_the_one_it_keeps(self) -> None:
        refused, crashed, kept = _refusing_client(), _serving_client(), _serving_client()
        launcher = MagicMock()
        launcher.launch.side_effect = [refused, crashed, kept]
        server = _server(max_consecutive_failures=3)

        # The backoff a call waits out before reviving a DEAD server is not this test's.
        with patch(GET_LAUNCHER, return_value=launcher), patch.object(server.health, "can_retry", return_value=True):
            with pytest.raises(McpServerStartError, match="handshake refused"):
                server.ensure_ready()
            assert server.dead_reason_snapshot == DEAD_START_FAILED

            server.invoke_tool("add", {})  # a call revives it
            crashed.is_alive.return_value = False
            server.invoke_tool("add", {})  # finds the crash, goes DEAD, and revives it again

        refused.close.assert_called_once_with()
        crashed.close.assert_called_once_with()
        kept.close.assert_not_called()
        assert server.state is McpServerState.READY
        assert server._client is kept
