"""The sessionless ``GET /mcp`` stream tells an HTTP client to re-list (#1366, part B).

Over a real socket: the front door ``serve --http`` serves, behind the auth
layer it mounts, is served by uvicorn on a loopback port, and each stream is
opened with httpx the way the TypeScript SDK client opens it after
``initialized``, with no session id. POSTs go through the harness's in-process
client; the stream does not touch the SDK's session manager, so the two do not
share a loop.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any

import httpx
import pytest
import uvicorn

from mcp_hangar.application.read_models.tool_projection import get_tool_projection_registry
from mcp_hangar.fastmcp_server import tool_list_changed, tool_list_changed_stream

from ._front_door_harness import SERVER, TENANT_A, TENANT_B, FrontDoor, front_door

HANDSHAKE = "2025-11-25"
LIST_CHANGED = '"method": "notifications/tools/list_changed"'
#: Past the coalescing window, with room for a loaded CI runner.
SETTLE_S = tool_list_changed.COALESCE_WINDOW_S * 4


@contextmanager
def _served(door: FrontDoor) -> Iterator[str]:
    """The front door's ASGI app on a real loopback socket; yields its base URL."""
    with closing(socket.socket()) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(door.client.app, host="127.0.0.1", port=port, lifespan="off", log_config=None)
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


class _Stream:
    """One open ``GET /mcp``: its status, its events as they arrive, and a count of its pings.

    The reading thread is the one that closes the connection. Closing it from
    another thread while this one is blocked reading does not send a FIN on
    Linux, so the gateway would never see the client leave. The thread checks
    for a stop at every frame, and the gateway pings often (see ``gateway``).
    """

    def __init__(self, base_url: str, headers: dict[str, str]) -> None:
        self.frames: queue.Queue[str] = queue.Queue()
        self.status: queue.Queue[int] = queue.Queue()
        self.pings = 0
        self._stop = threading.Event()
        self._client = httpx.Client(timeout=httpx.Timeout(10.0, read=None))
        self._thread = threading.Thread(target=self._read, args=(base_url, headers), daemon=True)
        self._thread.start()
        self.code = self.status.get(timeout=10)

    def _read(self, base_url: str, headers: dict[str, str]) -> None:
        try:
            with self._client.stream("GET", f"{base_url}/mcp", headers=headers) as response:
                self.status.put(response.status_code)
                for chunk in response.iter_text():
                    for frame in chunk.replace("\r\n", "\n").split("\n\n"):
                        if frame.startswith(":"):
                            self.pings += 1
                        elif frame.strip():
                            self.frames.put(frame)
                    if self._stop.is_set():
                        return
        except httpx.HTTPError:
            pass
        finally:
            self.status.put(-1)
            self.frames.put("<closed>")

    def next(self, timeout: float = 5.0) -> str | None:
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        self._client.close()


def _headers(door: FrontDoor, tenant: str, **extra: str) -> dict[str, str]:
    return {"Accept": "text/event-stream", "X-API-Key": door.keys[tenant], "MCP-Protocol-Version": HANDSHAKE, **extra}


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FrontDoor, str]]:
    # Often, so a client's reading thread wakes to close its own connection.
    monkeypatch.setattr(tool_list_changed_stream, "KEEPALIVE_S", 0.2)
    with front_door(("read_item", "write_item")) as door, _served(door) as base_url:
        yield door, base_url


def _open(gateway: tuple[FrontDoor, str], tenant: str, **extra: str) -> _Stream:
    door, base_url = gateway
    return _Stream(base_url, _headers(door, tenant, **extra))


def _wait_for_no_channels() -> bool:
    deadline = time.monotonic() + 5
    while tool_list_changed._channels and time.monotonic() < deadline:
        time.sleep(0.05)
    return not tool_list_changed._channels


def test_a_stream_is_told_on_open_and_again_only_when_its_tenant_changes(gateway: tuple[FrontDoor, str]) -> None:
    a, b = _open(gateway, TENANT_A), _open(gateway, TENANT_B)
    try:
        assert (a.code, b.code) == (200, 200)
        # Once as it opens: whatever landed between the client's listing and the GET.
        assert LIST_CHANGED in (a.next() or "")
        assert LIST_CHANGED in (b.next() or "")

        get_tool_projection_registry().withdraw(SERVER, "write_item", TENANT_A)

        assert LIST_CHANGED in (a.next() or ""), "tenant A was not told its projection changed"
        assert b.next(timeout=SETTLE_S) is None, "tenant B was told about a change to tenant A's projection"
    finally:
        a.close()
        b.close()


def test_the_channel_goes_when_the_client_leaves(gateway: tuple[FrontDoor, str]) -> None:
    stream = _open(gateway, TENANT_A)
    assert stream.code == 200
    assert len(tool_list_changed._channels) == 1

    stream.close()

    assert _wait_for_no_channels()


def test_an_idle_stream_carries_keep_alive_comments(gateway: tuple[FrontDoor, str]) -> None:
    stream = _open(gateway, TENANT_A)
    try:
        assert LIST_CHANGED in (stream.next() or "")
        deadline = time.monotonic() + 5
        while not stream.pings and time.monotonic() < deadline:
            time.sleep(0.05)
        # SSE comments: no event reaches the client between notifications.
        assert stream.pings
        assert stream.next(timeout=0.5) is None
    finally:
        stream.close()


def test_a_principal_past_its_cap_is_refused_and_others_are_not(
    gateway: tuple[FrontDoor, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool_list_changed, "MAX_STREAMS_PER_PRINCIPAL", 1)
    first = _open(gateway, TENANT_A)
    second = _open(gateway, TENANT_A)
    other = _open(gateway, TENANT_B)
    try:
        assert (first.code, second.code, other.code) == (200, 429, 200)
    finally:
        for stream in (first, second, other):
            stream.close()


def test_a_suspended_session_is_refused_and_its_open_stream_ended(
    gateway: tuple[FrontDoor, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_hangar.server.api.sessions import get_session_suspension_registry

    # The peer is loopback, which is a trusted proxy by default, so its x-session-id is honoured.
    stream = _open(gateway, TENANT_A, **{"x-session-id": "session-one"})
    registry = get_session_suspension_registry()
    try:
        assert stream.code == 200
        registry.suspend("session-one")

        frames = [stream.next() for _ in range(3)]
        assert "<closed>" in frames, frames
        assert _wait_for_no_channels()

        refused = _open(gateway, TENANT_A, **{"x-session-id": "session-one"})
        assert refused.code == 403
        refused.close()
    finally:
        registry.unsuspend("session-one")
        stream.close()


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"Accept": "application/json"}, 406),
        ({"Host": "attacker.example"}, 421),
        ({"X-API-Key": "not-a-key"}, 401),
    ],
)
def test_a_stream_is_not_opened_for_a_request_the_post_path_would_refuse(
    gateway: tuple[FrontDoor, str], headers: dict[str, str], status: int
) -> None:
    stream = _open(gateway, TENANT_A, **headers)
    try:
        assert stream.code == status
        assert not tool_list_changed._channels
    finally:
        stream.close()


def test_egress_serves_no_stream() -> None:
    with front_door(("read_item",), topology="egress") as door, _served(door) as base_url:
        stream = _Stream(base_url, _headers(door, TENANT_A))
        try:
            assert stream.code == 405
        finally:
            stream.close()


def _initialize(door: FrontDoor) -> dict[str, Any]:
    from ._front_door_harness import jsonrpc

    params = {"protocolVersion": HANDSHAKE, "capabilities": {}, "clientInfo": {"name": "probe", "version": "0"}}
    return jsonrpc(door.post(TENANT_A, "initialize", params, era=HANDSHAKE))


def test_egress_does_not_advertise_list_changed() -> None:
    with front_door(("read_item",), topology="egress") as door:
        assert _initialize(door)["result"]["capabilities"]["tools"].get("listChanged") is not True


def test_a_tenant_past_its_cap_is_refused_and_other_tenants_are_not(
    gateway: tuple[FrontDoor, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool_list_changed, "MAX_STREAMS_PER_TENANT", 1)
    first = _open(gateway, TENANT_A)
    second = _open(gateway, TENANT_A)
    other = _open(gateway, TENANT_B)
    try:
        assert (first.code, second.code, other.code) == (200, 429, 200)
    finally:
        for stream in (first, second, other):
            stream.close()


def test_a_stream_ends_at_its_lifetime_so_the_caller_authenticates_again(
    gateway: tuple[FrontDoor, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tool_list_changed_stream, "MAX_LIFETIME_S", 0.5)
    stream = _open(gateway, TENANT_A)
    try:
        assert stream.code == 200
        frames = [stream.next() for _ in range(3)]
        assert "<closed>" in frames, frames
        assert _wait_for_no_channels()
    finally:
        stream.close()


def test_a_projection_that_cannot_be_generated_opens_no_stream(
    gateway: tuple[FrontDoor, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_hangar.fastmcp_server import flat_tool_projection

    def fail(tenant_id: str | None) -> Any:
        raise RuntimeError("generation failed")

    monkeypatch.setattr(flat_tool_projection, "generate_projection", fail)
    stream = _open(gateway, TENANT_A)
    try:
        assert stream.code == 503
        assert not tool_list_changed._channels
    finally:
        stream.close()


def test_a_revocation_ends_only_that_principals_streams(gateway: tuple[FrontDoor, str]) -> None:
    from mcp_hangar.domain.events import ApiKeyRevoked
    from mcp_hangar.server.context import get_context

    bus = get_context().runtime.event_bus
    tool_list_changed_stream.subscribe_revocations(bus)  # as `init_event_handlers` wires it
    a, b = _open(gateway, TENANT_A), _open(gateway, TENANT_B)
    try:
        assert (a.code, b.code) == (200, 200)
        assert LIST_CHANGED in (a.next() or "")
        assert LIST_CHANGED in (b.next() or "")

        # The harness seeds tenant A's key for principal `agent-a`.
        bus.publish(ApiKeyRevoked(key_id="key-a", principal_id="agent-a", revoked_by="operator"))

        assert a.next() == "<closed>", "the revoked principal's stream stayed open"
        assert b.next(timeout=SETTLE_S) is None, "another principal's stream was ended"
    finally:
        a.close()
        b.close()


def test_the_streams_and_their_notifications_are_counted(gateway: tuple[FrontDoor, str]) -> None:
    from mcp_hangar import metrics as prometheus_metrics

    def sent() -> float:
        return sum(
            sample.value
            for sample in prometheus_metrics.TOOL_LIST_CHANGED_NOTIFICATIONS_TOTAL.collect()
            if sample.labels.get("transport") == "http"
        )

    def open_streams() -> float:
        return sum(sample.value for sample in prometheus_metrics.TOOL_LIST_CHANGED_STREAMS.collect())

    stream = _open(gateway, TENANT_A)
    try:
        assert LIST_CHANGED in (stream.next() or "")
        assert open_streams() == 1
        before = sent()

        get_tool_projection_registry().withdraw(SERVER, "write_item", TENANT_A)

        assert LIST_CHANGED in (stream.next() or "")
        assert sent() == before + 1
    finally:
        stream.close()
    assert _wait_for_no_channels()
    assert open_streams() == 0
