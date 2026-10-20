"""A key-pinned server's connection pool is replaced every `max_connection_age` (#1945, ADR-030 §8).

httpcore has no maximum connection age, only an idle expiry, so a connection
verified once could serve for as long as it stayed busy. Replacing the pool
makes every connection handshake again against the current key list. The MCP
session lives on the client, not the pool, so it survives; a request in flight
finishes on the old pool before that pool is closed; the GET stream re-opens on
the new one at once.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from mcp_hangar.http_client import HttpClient, HttpClientConfig
from tests.unit.test_an_upstream_is_pinned_by_its_key import _leaf

PIN = "sha256:" + "0" * 64


class _KeepAliveUpstream:
    """A TLS server that keeps each connection open for many requests and counts connections."""

    def __init__(self, cert: str, key: str) -> None:
        self.connections = 0
        self._context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._context.load_cert_chain(cert, key)
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.2)
        self.port = self._listener.getsockname()[1]
        self._running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while self._running:
            try:
                raw, _ = self._listener.accept()
            except OSError:
                continue
            self.connections += 1
            threading.Thread(target=self._serve, args=(raw,), daemon=True).start()

    def _serve(self, raw: socket.socket) -> None:
        try:
            conn = self._context.wrap_socket(raw, server_side=True)
            conn.settimeout(5)
            buffer = b""
            while self._running:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\r\n\r\n" in buffer:
                    head, _, rest = buffer.partition(b"\r\n\r\n")
                    length = next(
                        (
                            int(line.split(b":")[1])
                            for line in head.split(b"\r\n")
                            if line.lower().startswith(b"content-length")
                        ),
                        0,
                    )
                    if len(rest) < length:
                        break
                    buffer = rest[length:]
                    body = b'{"jsonrpc":"2.0","id":"1","result":{}}'
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n%s"
                        % (len(body), body)
                    )
        except (OSError, ssl.SSLError):
            return

    def stop(self) -> None:
        self._running = False
        self._listener.close()


class TestAReplacedPoolHandshakesAgain:
    def test_calls_share_a_connection_until_the_pool_is_replaced(self, tmp_path) -> None:
        cert, key, pin = _leaf(tmp_path, "upstream")
        upstream = _KeepAliveUpstream(cert, key)
        client = HttpClient(
            endpoint=f"https://127.0.0.1:{upstream.port}",
            http_config=HttpClientConfig(peer_key_pins=frozenset({pin}), max_retries=0),
        )
        try:
            client.call("tools/list", {})
            client.call("tools/list", {})
            before = upstream.connections
            client._replace_pool()
            client.call("tools/list", {})
        finally:
            client.close()
            upstream.stop()

        assert before == 1
        assert upstream.connections == 2


def _client(age: float | None) -> HttpClient:
    return HttpClient(
        endpoint="https://upstream:8443",
        http_config=HttpClientConfig(peer_key_pins=frozenset({PIN}), max_connection_age=age),
    )


class TestTheReplacement:
    def test_the_timer_replaces_the_pool_and_keeps_the_session(self) -> None:
        client = _client(0.05)
        client._mcp_session_id = "session-1"
        first = client._client
        try:
            deadline = time.monotonic() + 5
            while client._client is first and time.monotonic() < deadline:
                time.sleep(0.01)
            replaced = client._client is not first
            session = client._mcp_session_id
        finally:
            client.close()

        assert replaced
        assert session == "session-1"
        assert first.is_closed

    def test_a_request_in_flight_finishes_on_the_old_pool_before_it_is_closed(self) -> None:
        client = _client(None)
        try:
            with client._lease() as old:
                client._replace_pool()
                assert old is not client._client
                assert not old.is_closed, "closed under a request still in flight"
            assert old.is_closed
        finally:
            client.close()

    def test_the_get_stream_is_closed_and_marked_for_an_immediate_reopen(self) -> None:
        client = _client(None)
        stream = MagicMock()
        client._stream_response = stream
        try:
            client._replace_pool()
        finally:
            client.close()

        stream.close.assert_called_once()
        assert client._stream_replaced is True

    def test_the_stream_loop_reopens_without_the_failure_backoff(self) -> None:
        client = _client(None)
        client._sse_running = True
        attempts: list[str] = []

        class _Stream:
            def __init__(self, status: int) -> None:
                self.status_code = status

            def __enter__(self):
                if not attempts:
                    attempts.append("replaced")
                    client._stream_replaced = True
                    raise httpx.ReadError("closed by the replacement")
                attempts.append("reopened")
                return self

            def __exit__(self, *exc) -> None:
                return None

        try:
            with (
                patch.object(client._client, "stream", side_effect=lambda *a, **k: _Stream(404)),
                patch("mcp_hangar.http_client.time.sleep", side_effect=AssertionError("backed off")),
            ):
                client._notification_stream_loop(lambda message: None)
        finally:
            client.close()

        assert attempts == ["replaced", "reopened"]

    def test_closing_the_client_cancels_the_timer_and_closes_every_pool(self) -> None:
        client = _client(3600)
        first_timer = client._rotation_timer
        with client._lease():
            client._replace_pool()
            retired = list(client._retired)
            timer = client._rotation_timer
            client.close()

        assert first_timer is not None and first_timer.finished.is_set(), "an early replacement restarts the clock"
        assert timer is not None and timer.finished.is_set()
        assert retired and all(pool.is_closed for pool in retired)

    def test_a_server_without_an_age_has_no_timer(self) -> None:
        client = HttpClient(endpoint="http://upstream:8080", http_config=HttpClientConfig())
        try:
            assert client._rotation_timer is None
        finally:
            client.close()


@pytest.mark.parametrize("age", [None])
def test_the_default_is_left_to_the_configuration(age) -> None:
    assert HttpClientConfig().max_connection_age is age
