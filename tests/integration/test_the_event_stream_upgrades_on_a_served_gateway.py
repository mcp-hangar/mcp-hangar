"""`/api/ws/events` upgrades on the gateway `serve --http` runs (#1676).

uvicorn upgrades a WebSocket connection only through a library it can import.
None was declared -- only the Dockerfile installed `websockets` -- so on a pip
or uv install uvicorn logged "No supported WebSocket library detected" and the
event stream, which the approval UI reads, answered 404.

This starts this checkout's `mcp-hangar serve --http` on a loopback port, in
the environment the declared dependencies built, with auth off, and connects
the way a client does: a subscribe message, answered by the ``subscribed`` ack.

The client is a raw RFC 6455 handshake and frame, not a WebSocket library, so
the test reads the same before the fix, when the environment has none: a 404
where the 101 should be.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from tests._hangar_executable import hangar_executable

_STARTUP_TIMEOUT_S = 30.0

_CONFIG = """\
logging:
  level: WARNING
mcp_servers: {}
"""


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[int]:
    """A running `serve --http`; yields its port once /health/live answers."""
    config = tmp_path / "config.yaml"
    config.write_text(_CONFIG)
    log_path = tmp_path / "hangar.log"
    port = _free_port()
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [hangar_executable(), "--config", str(config), "serve", "--http"]
            + ["--host", "127.0.0.1", "--port", str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
            cwd=str(tmp_path),
        )
    try:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while True:
            # Failing, not skipping: a skip here is a green run that served nothing.
            assert proc.poll() is None, f"serve --http exited {proc.returncode}:\n{log_path.read_text()[-4000:]}"
            assert time.monotonic() < deadline, f"serve --http never became live:\n{log_path.read_text()[-4000:]}"
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health/live", timeout=1.0).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        yield port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _read_exactly(sock: socket.socket, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        assert chunk, f"connection closed after {data!r}"
        data += chunk
    return data


def _send_text(sock: socket.socket, text: str) -> None:
    """One masked text frame, as a client must send it (RFC 6455 5.3)."""
    payload = text.encode()
    assert len(payload) < 126
    mask = os.urandom(4)
    sock.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_text(sock: socket.socket) -> str:
    """One unmasked text frame from the server."""
    first, second = _read_exactly(sock, 2)
    assert first == 0x81, f"expected a final text frame, got opcode byte {first:#x}"
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exactly(sock, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exactly(sock, 8), "big")
    return _read_exactly(sock, length).decode()


def test_a_subscriber_is_upgraded_and_acknowledged(gateway: int) -> None:
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(("127.0.0.1", gateway), timeout=10) as sock:
        sock.sendall(
            (
                "GET /api/ws/events HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{gateway}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head = b""
        while b"\r\n\r\n" not in head:
            head += _read_exactly(sock, 1)
        status_line = head.split(b"\r\n", 1)[0].decode()
        assert status_line.startswith("HTTP/1.1 101"), head.decode(errors="replace")

        _send_text(sock, json.dumps({"type": "subscribe", "event_types": ["McpServerStarted"]}))
        ack = json.loads(_recv_text(sock))

    assert ack == {"type": "subscribed", "event_types": ["McpServerStarted"], "mcp_server_ids": []}
