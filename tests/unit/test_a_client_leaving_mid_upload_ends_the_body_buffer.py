"""A client that disconnects mid-upload ends the SEP-2243 body buffer (#2123).

`_buffer_body` skipped every message that was not `http.request`. After a
disconnect every `receive()` returns `http.disconnect`, so the loop spun on it
for good. uvicorn's `receive()` answers a disconnected client without
yielding, so the spin never gave the event loop back: the whole gateway stopped
serving, not only that connection. Before the fix the first test here hangs
until the suite's timeout, for the same reason `wait_for` cannot cancel it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_hangar.fastmcp_server.front_door_routing import FrontDoorRoutingMiddleware

_SCOPE = {
    "type": "http",
    "method": "POST",
    "path": "/mcp",
    "headers": [(b"content-type", b"application/json"), (b"mcp-method", b"tools/list")],
}


def _receive_then_disconnect(chunks: list[bytes]):
    queue = [{"type": "http.request", "body": chunk, "more_body": True} for chunk in chunks]
    calls = {"after_disconnect": 0}

    async def receive() -> dict[str, Any]:
        if queue:
            return queue.pop(0)
        calls["after_disconnect"] += 1
        return {"type": "http.disconnect"}

    return receive, calls


def test_a_disconnect_mid_body_returns_without_calling_the_app_or_answering() -> None:
    reached: list[Any] = []
    sent: list[dict[str, Any]] = []

    async def app(scope, receive, send) -> None:
        reached.append(scope)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    receive, calls = _receive_then_disconnect([b'{"jsonrpc": "2.0", '])

    asyncio.run(asyncio.wait_for(FrontDoorRoutingMiddleware(app)(dict(_SCOPE), receive, send), timeout=2))

    assert reached == []
    assert sent == []
    assert calls["after_disconnect"] == 1


def test_a_complete_body_still_reaches_the_app() -> None:
    reached: list[bytes] = []

    async def app(scope, receive, send) -> None:
        message = await receive()
        reached.append(message["body"])

    async def send(message: dict[str, Any]) -> None:
        pass

    body = b'{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}'
    chunks = iter([{"type": "http.request", "body": body, "more_body": False}])

    async def receive() -> dict[str, Any]:
        return next(chunks, {"type": "http.disconnect"})

    asyncio.run(asyncio.wait_for(FrontDoorRoutingMiddleware(app)(dict(_SCOPE), receive, send), timeout=2))

    assert reached == [body]
