"""Focused tests for websocket auth and backpressure safeguards."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from mcp_hangar.domain.exceptions import MissingCredentialsError
from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.lifecycle import ServerLifecycle
from mcp_hangar.server.api.ws.manager import EventStreamQueue


def test_websocket_without_valid_auth_is_rejected() -> None:
    """Auth wrapper should reject unauthenticated websocket connections."""
    mock_runtime = MagicMock()
    mock_context = ApplicationContext(runtime=mock_runtime, mcp_server=MagicMock())
    lifecycle = ServerLifecycle(mock_context)

    auth_components = MagicMock()
    auth_components.authn_middleware.authenticate.side_effect = MissingCredentialsError()
    inner_app = AsyncMock()
    auth_app = lifecycle._create_auth_app(inner_app, auth_components)

    sent_messages = []

    async def send(message):
        sent_messages.append(message)

    scope = {
        "type": "websocket",
        "path": "/api/ws/events",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "query_string": b"",
    }

    asyncio.run(auth_app(scope, AsyncMock(), send))

    inner_app.assert_not_called()
    assert sent_messages == [{"type": "websocket.close", "code": 1008, "reason": "No credentials provided"}]


def test_backpressure_drop_behavior_keeps_newest_event() -> None:
    """Overflow should drop the oldest queued event and retain the newest."""

    async def scenario() -> None:
        drops = []
        queue = EventStreamQueue(maxsize=1, on_drop=lambda dropped, new: drops.append((dropped, new)))
        loop = asyncio.get_running_loop()
        first = object()
        second = object()

        queue.put_threadsafe(first, loop)
        await asyncio.sleep(0)
        queue.put_threadsafe(second, loop)
        await asyncio.sleep(0)

        assert drops == [(first, second)]
        assert queue.queue.qsize() == 1
        assert queue.queue.get_nowait() is second

    asyncio.run(scenario())
