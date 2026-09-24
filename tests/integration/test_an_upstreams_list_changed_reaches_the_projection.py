"""An upstream's own ``tools/list_changed`` changes what the front door lists (#1366).

The upstream announces a changed catalogue on its standing GET stream (#882).
The aggregate re-listed it, but the front door serves the tool projection
registry, which only a start filled. So a client told to re-list got the
catalogue from the last start: a tool the upstream added was missing and a tool
it removed was still listed.

Everything here is real except the upstream: the served front door behind its
authentication layer, a remote ``McpServer`` started through the command bus
with its real HTTP client and GET stream, the projection registry, and the
handler wired the way bootstrap wires it. The upstream is an in-process HTTP
server that serves a GET stream and pushes ``notifications/tools/list_changed``
down it when the test says so.

Naming: neutral placeholders only (store, read_item, write_item, delete_item,
tenant:a, tenant:b).
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable
from typing import ClassVar

import pytest

from ._front_door_harness import TENANT_A, TENANT_B, FrontDoor, Upstream, front_door

LIST_CHANGED = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}


class StreamingUpstream(Upstream):
    """The harness's upstream, plus the standing GET stream it can push down."""

    pushes: ClassVar[queue.Queue[dict[str, object]]]
    stream_open: ClassVar[threading.Event]

    def do_GET(self) -> None:  # noqa: N802 -- http.server's handler name
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.stream_open.set()
        while True:
            try:
                message = self.pushes.get(timeout=0.1)
                frame = f"data: {json.dumps(message)}\n\n"
            except queue.Empty:
                # A comment keeps the stream alive and finds a closed client.
                frame = ": keep-alive\n\n"
            try:
                self.wfile.write(frame.encode())
                self.wfile.flush()
            except OSError:
                return


def _streaming_upstream() -> type[StreamingUpstream]:
    """A fresh upstream class, so no queue or event outlives its test."""
    return type(
        "_StreamingUpstream",
        (StreamingUpstream,),
        {"pushes": queue.Queue(), "stream_open": threading.Event()},
    )


def _eventually(probe: Callable[[], object], expected: object, timeout_s: float = 5.0) -> object:
    """Poll *probe* until it returns *expected*; return the last value either way."""
    deadline = time.monotonic() + timeout_s
    seen = probe()
    while seen != expected and time.monotonic() < deadline:
        time.sleep(0.05)
        seen = probe()
    return seen


def _change_catalogue(door: FrontDoor, tools: tuple[str, ...]) -> None:
    """The upstream now lists *tools* and says so on its GET stream."""
    upstream = door.upstream
    assert issubclass(upstream, StreamingUpstream)
    assert upstream.stream_open.wait(5.0), "the gateway never opened the upstream's GET stream"
    upstream.tools = tools
    upstream.pushes.put(LIST_CHANGED)


@pytest.fixture
def upstream_class() -> type[StreamingUpstream]:
    return _streaming_upstream()


def test_a_relist_after_list_changed_has_the_added_tool_and_not_the_removed_one(
    upstream_class: type[StreamingUpstream],
) -> None:
    with front_door(("read_item", "write_item"), upstream_class=upstream_class) as door:
        assert door.names(TENANT_A) == ["read_item", "write_item"]

        _change_catalogue(door, ("read_item", "delete_item"))

        assert _eventually(lambda: door.names(TENANT_A), ["delete_item", "read_item"]) == [
            "delete_item",
            "read_item",
        ]


def test_the_handshake_era_relist_sees_the_change_too(upstream_class: type[StreamingUpstream]) -> None:
    """The projection is one registry: both protocol eras list from it."""
    from ._front_door_harness import LEGACY

    with front_door(("read_item", "write_item"), upstream_class=upstream_class) as door:
        _change_catalogue(door, ("read_item", "delete_item"))

        assert _eventually(lambda: door.names(TENANT_A, era=LEGACY), ["delete_item", "read_item"]) == [
            "delete_item",
            "read_item",
        ]


def test_the_added_tool_is_callable_through_the_front_door(upstream_class: type[StreamingUpstream]) -> None:
    with front_door(("read_item",), upstream_class=upstream_class) as door:
        _change_catalogue(door, ("read_item", "delete_item"))
        _eventually(lambda: door.names(TENANT_A), ["delete_item", "read_item"])

        door.result(TENANT_A, "delete_item")

        assert door.upstream.called == ["delete_item"]


def test_a_tenant_still_sees_only_what_its_policy_allows_after_the_change(
    upstream_class: type[StreamingUpstream],
) -> None:
    """The rebuilt projection is the same per-tenant projection a start builds.

    Tenant B is allowed ``read_item`` only. The upstream adding a tool changes
    tenant A's listing and leaves tenant B's exactly as it was.
    """
    policies = {TENANT_B: ("read_item",)}
    with front_door(("read_item", "write_item"), policies, upstream_class=upstream_class) as door:
        assert door.names(TENANT_B) == ["read_item"]

        _change_catalogue(door, ("read_item", "write_item", "delete_item"))

        assert _eventually(lambda: door.names(TENANT_A), ["delete_item", "read_item", "write_item"]) == [
            "delete_item",
            "read_item",
            "write_item",
        ]
        assert door.names(TENANT_B) == ["read_item"]
