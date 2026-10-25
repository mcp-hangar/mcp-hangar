"""A stopping `serve --http` answers the MCP calls in flight, within its bound (#2097).

On SIGTERM sse-starlette closed the POST stream a pending `tools/call` answers
on, so the client got a cut connection while the call ran on to completion
upstream -- a client that retried ran it twice. And the batch's worker thread
held the process open for as long as the slowest call, whatever
`http.graceful_shutdown_timeout_s` said.

The real `serve --http` runs here in its own process, with a slow raw JSON-RPC
upstream (`tests/slow_provider.py`) and an HTTP client that calls `hangar_call`
the way an MCP client does, and SIGTERM arrives while the call is in flight
upstream.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests._hangar_executable import hangar_executable

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

_PROVIDER = Path(__file__).resolve().parents[1] / "slow_provider.py"
_STARTUP_TIMEOUT_S = 30.0
_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_MODERN = "2026-07-28"
#: The per-request negotiation a 2026-07-28 request carries instead of a handshake.
_MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _modern_headers(method: str, name: str | None = None) -> dict[str, str]:
    return dict(
        _HEADERS, **{"MCP-Protocol-Version": _MODERN, "Mcp-Method": method}, **({"Mcp-Name": name} if name else {})
    )


def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Gateway:
    proc: subprocess.Popen[bytes]
    url: str
    upstream_log: Path
    log: Path

    def upstream_lines(self) -> list[str]:
        return self.upstream_log.read_text().splitlines() if self.upstream_log.exists() else []


def _start(tmp_path: Path, bound_s: int | None, *, front_door: bool = False) -> Gateway:
    upstream_log = tmp_path / "upstream.log"
    http_section = f"http:\n  graceful_shutdown_timeout_s: {bound_s}\n" if bound_s is not None else ""
    if front_door:  # the mode that serves the `list_changed` GET stream
        http_section += "tool_access:\n  mode: front_door\n"
    config = tmp_path / "config.yaml"
    config.write_text(
        "mcp_servers:\n"
        "  slow:\n"
        "    mode: subprocess\n"
        f"    command: {json.dumps([sys.executable, str(_PROVIDER), str(upstream_log)])}\n"
        "event_store:\n"
        "  driver: memory\n" + http_section
    )
    log = tmp_path / "hangar.log"
    port = _free_port()
    with log.open("wb") as out:
        proc = subprocess.Popen(
            [hangar_executable(), "--config", str(config), "serve", "--http", "--port", str(port)],
            stdout=out,
            stderr=subprocess.STDOUT,
            cwd=str(tmp_path),
            env=dict(os.environ, MCP_TRACING_ENABLED="false"),
        )
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while True:
        assert proc.poll() is None, f"serve --http exited {proc.returncode}:\n{log.read_text()[-4000:]}"
        assert time.monotonic() < deadline, f"serve --http never became live:\n{log.read_text()[-4000:]}"
        try:
            if httpx.get(f"http://127.0.0.1:{port}/health/live", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    return Gateway(proc, f"http://127.0.0.1:{port}/mcp", upstream_log, log)


@pytest.fixture
def gateways() -> Iterator[list[Gateway]]:
    started: list[Gateway] = []
    yield started
    for gateway in started:
        if gateway.proc.poll() is None:
            gateway.proc.kill()
            gateway.proc.wait()


def _call_in_background(
    gateway: Gateway, seconds: float, *, modern: bool = False
) -> tuple[threading.Thread, dict[str, Any]]:
    """Call `sleep` through `hangar_call` on a thread; the dict gets the JSON-RPC answer, or the error.

    `modern` calls on the 2026-07-28 era: no handshake, and an answer that is a
    plain JSON body unless the call runs long enough to commit to a stream.
    """
    outcome: dict[str, Any] = {}
    client = httpx.Client(timeout=60)
    params: dict[str, Any] = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": "slow", "tool": "sleep", "arguments": {"seconds": seconds}}]},
    }
    if modern:
        headers = _modern_headers("tools/call", "hangar_call")
        params["_meta"] = _MODERN_META
    else:
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "1"},
            },
        }
        client.post(gateway.url, headers=_HEADERS, json=init).raise_for_status()
        headers = dict(_HEADERS, **{"mcp-protocol-version": "2025-06-18"})
        client.post(gateway.url, headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}

    def run() -> None:
        try:
            with client.stream("POST", gateway.url, headers=headers, json=call) as response:
                body = b"".join(response.iter_bytes()).decode()
            outcome["status"] = response.status_code
            outcome["content_type"] = response.headers.get("content-type", "")
            if outcome["content_type"].startswith("application/json"):
                outcome["messages"] = [json.loads(body)]
            else:
                frames = [line[len("data: ") :] for line in body.splitlines() if line.startswith("data: ")]
                outcome["messages"] = [json.loads(frame) for frame in frames]
        except httpx.HTTPError as exc:
            outcome["error"] = f"{type(exc).__name__}: {exc}"
        outcome["at"] = time.monotonic()
        client.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def _stop_once_in_flight(gateway: Gateway) -> float:
    """SIGTERM the gateway as soon as the call has reached the upstream; return when."""
    deadline = time.monotonic() + 20
    while not any(line.startswith("started") for line in gateway.upstream_lines()):
        assert time.monotonic() < deadline, f"the call never reached the upstream:\n{gateway.log.read_text()[-4000:]}"
        time.sleep(0.02)
    gateway.proc.send_signal(signal.SIGTERM)
    return time.monotonic()


def _exit_after(gateway: Gateway, sigterm_at: float, timeout_s: float) -> float:
    try:
        rc = gateway.proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        pytest.fail(f"serve --http still running {timeout_s}s after SIGTERM:\n{gateway.log.read_text()[-4000:]}")
    assert rc == 0, gateway.log.read_text()[-4000:]
    return time.monotonic() - sigterm_at


def _result_of(outcome: dict[str, Any]) -> dict[str, Any]:
    assert "error" not in outcome, outcome
    [message] = outcome["messages"]
    assert message["id"] == 7 and "result" in message, message
    return dict(message["result"]["structuredContent"])


def test_an_unbounded_stop_delivers_the_call_and_runs_it_once(tmp_path: Path, gateways: list[Gateway]) -> None:
    gateway = _start(tmp_path, bound_s=None)
    gateways.append(gateway)
    thread, outcome = _call_in_background(gateway, seconds=2.5)
    sigterm_at = _stop_once_in_flight(gateway)

    thread.join(30)
    exited_after = _exit_after(gateway, sigterm_at, timeout_s=30)

    result = _result_of(outcome)
    assert result["success"] is True, result
    assert outcome["content_type"].startswith("text/event-stream")
    assert [line for line in gateway.upstream_lines() if line == "completed"] == ["completed"]
    # It waited for the call, and then exited.
    assert outcome["at"] - sigterm_at > 2.0
    assert exited_after < 2.5 + 8


def test_a_call_that_finishes_within_the_bound_is_delivered(tmp_path: Path, gateways: list[Gateway]) -> None:
    gateway = _start(tmp_path, bound_s=20)
    gateways.append(gateway)
    thread, outcome = _call_in_background(gateway, seconds=1.0)
    sigterm_at = _stop_once_in_flight(gateway)

    thread.join(30)
    exited_after = _exit_after(gateway, sigterm_at, timeout_s=30)

    assert _result_of(outcome)["success"] is True
    assert gateway.upstream_lines().count("completed") == 1
    # Done when the call is, not when the bound runs out.
    assert exited_after < 8


@pytest.mark.parametrize("modern", [False, True], ids=["2025-06-18", "2026-07-28"])
def test_a_call_longer_than_the_bound_gets_an_error_and_the_gateway_exits(
    tmp_path: Path, gateways: list[Gateway], modern: bool
) -> None:
    bound_s = 1
    gateway = _start(tmp_path, bound_s=bound_s)
    gateways.append(gateway)
    thread, outcome = _call_in_background(gateway, seconds=30, modern=modern)
    sigterm_at = _stop_once_in_flight(gateway)

    thread.join(30)
    exited_after = _exit_after(gateway, sigterm_at, timeout_s=30)

    # A JSON-RPC error for the call on its own stream, not a cut connection.
    assert "error" not in outcome, outcome
    [message] = outcome["messages"]
    assert message["id"] == 7
    assert message["error"]["code"] == -32000, message
    assert message["error"]["data"]["reason"] == "shutdown"
    # Each era's own format: the handshake era's stream, the modern era's JSON body.
    assert outcome["content_type"].startswith("application/json" if modern else "text/event-stream")
    assert bound_s <= outcome["at"] - sigterm_at < bound_s + 3
    assert exited_after < bound_s + 6
    # The upstream call was abandoned, not run to completion: its server is gone.
    [pid] = [int(line.split()[1]) for line in gateway.upstream_lines() if line.startswith("started")]
    assert not _alive(pid, within_s=5)
    assert "completed" not in gateway.upstream_lines()


def _alive(pid: int, within_s: float) -> bool:
    """Whether *pid* is still running *within_s* from now."""
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        time.sleep(0.05)
    return True


def test_an_open_list_changed_stream_does_not_hold_an_unbounded_stop(tmp_path: Path, gateways: list[Gateway]) -> None:
    # sse-starlette no longer ends this stream on its own; the drain has to.
    gateway = _start(tmp_path, bound_s=None, front_door=True)
    gateways.append(gateway)
    headers = {"Accept": "text/event-stream", "mcp-protocol-version": "2025-06-18"}
    with httpx.Client(timeout=30) as client, client.stream("GET", gateway.url, headers=headers) as stream:
        assert stream.status_code == 200
        lines = stream.iter_lines()
        assert any(line.startswith("data: ") for line in lines)  # told on open
        sigterm_at = time.monotonic()
        gateway.proc.send_signal(signal.SIGTERM)
        try:
            for _ in lines:
                pass
        except httpx.RemoteProtocolError:
            pass  # sse-starlette ends an idle stream without its last chunk, as it always did
        ended_after = time.monotonic() - sigterm_at

    assert ended_after < 3
    assert _exit_after(gateway, sigterm_at, timeout_s=30) < 5


def test_an_open_listen_stream_does_not_lengthen_a_bounded_stop(tmp_path: Path, gateways: list[Gateway]) -> None:
    # A 2026-07-28 `subscriptions/listen` POST stays open; it is not a call to
    # wait for, so the stop ends at the bound as it did before, not after it.
    bound_s = 2
    gateway = _start(tmp_path, bound_s=bound_s, front_door=True)
    gateways.append(gateway)
    listen = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "subscriptions/listen",
        "params": {"notifications": {"toolsListChanged": True}, "_meta": _MODERN_META},
    }
    acknowledged = threading.Event()

    def read() -> None:  # a listening client reads all along
        with httpx.Client(timeout=30) as client:
            try:
                headers = _modern_headers("subscriptions/listen")
                with client.stream("POST", gateway.url, headers=headers, json=listen) as stream:
                    for line in stream.iter_lines():
                        if line.startswith("data: "):
                            acknowledged.set()
            except httpx.HTTPError:
                pass

    threading.Thread(target=read, daemon=True).start()
    assert acknowledged.wait(10)
    sigterm_at = time.monotonic()
    gateway.proc.send_signal(signal.SIGTERM)
    exited_after = _exit_after(gateway, sigterm_at, timeout_s=30)

    assert exited_after < bound_s + 1.5
    assert "http_shutdown_abandoned_calls" not in gateway.log.read_text()
