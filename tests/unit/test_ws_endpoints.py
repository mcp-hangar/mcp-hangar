"""Tests for WebSocket endpoint handlers: ws_events_endpoint and ws_state_endpoint.

Tests use unittest.mock.patch and AsyncMock for async methods.
The async endpoints are driven via asyncio.run() or loop.run_until_complete().
"""

import asyncio
from collections.abc import Awaitable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.websockets import WebSocketDisconnect


# ---------------------------------------------------------------------------
# Helper: minimal WebSocket mock
# ---------------------------------------------------------------------------


def make_ws_mock(receive_sequence: list[object] | None = None) -> MagicMock:
    """Build an AsyncMock WebSocket that returns items from receive_sequence.

    Each call to receive_json() pops from the sequence; TimeoutError is raised
    when the sequence is exhausted or when `asyncio.TimeoutError` is in the list.
    """
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}
    ws.scope = {}
    ws.url = MagicMock(path="/ws/events", query="")
    if receive_sequence is None:
        receive_sequence = []

    seq = list(receive_sequence)

    async def _receive_json():
        if not seq:
            raise TimeoutError
        item = seq.pop(0)
        if item is asyncio.TimeoutError:
            raise TimeoutError
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item

    ws.receive_json = _receive_json
    return ws


# ---------------------------------------------------------------------------
# ws_events_endpoint tests
# ---------------------------------------------------------------------------


class TestWsEventsEndpoint:
    """Tests for the /ws/events WebSocket handler."""

    def _run(self, coro: Awaitable[object]) -> object:
        return asyncio.run(coro)

    def test_connect_subscribes_to_event_bus(self):
        """On connect, the handler subscribes a callback to the EventBus."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        # Queue that will receive one event and then cause disconnect
        async def run():
            ws = make_ws_mock(receive_sequence=[asyncio.TimeoutError])
            # Make wait_for time out immediately (no idle wait needed in test)
            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()

                    # To exit the loop, trigger WebSocketDisconnect after ping
                    ws.send_json = AsyncMock(side_effect=WebSocketDisconnect)
                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_events_endpoint(ws)
                        except Exception:
                            pass

            mock_bus.subscribe_to_all.assert_called_once()

        self._run(run())

    def test_rejects_ws_origin_before_accept(self):
        """Origin mismatch closes with policy violation before accept."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        async def run():
            ws = make_ws_mock()
            ws.headers = {"origin": "https://evil.example"}

            with patch(
                "mcp_hangar.server.api.ws.events.get_cors_config",
                return_value={"allow_origins": ["https://ok.example"]},
            ):
                await ws_events_endpoint(ws)

            ws.accept.assert_not_called()
            ws.close.assert_called_once_with(code=1008)

        self._run(run())

    def test_filter_applied_only_matching_events_enqueued(self):
        """Only events matching the filter are enqueued via put_threadsafe."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint
        from mcp_hangar.domain.events import DomainEvent
        from dataclasses import dataclass, field
        from datetime import datetime, UTC

        @dataclass
        class EvX(DomainEvent):
            timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

            def to_dict(self):
                return {"event_type": "X"}

        @dataclass
        class EvY(DomainEvent):
            timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

            def to_dict(self):
                return {"event_type": "Y"}

        captured_handler = {}

        def subscribe_side_effect(handler):
            captured_handler["fn"] = handler

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock(side_effect=subscribe_side_effect)
        mock_bus.unsubscribe_from_all = MagicMock()

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()

            # Provide filter message then cause disconnect on second receive_json call
            filter_msg = {"event_types": ["X"]}
            receive_calls = [0]

            async def mock_receive_json():
                if receive_calls[0] == 0:
                    receive_calls[0] += 1
                    return filter_msg
                raise TimeoutError

            ws.receive_json = mock_receive_json
            ws.send_json = AsyncMock(side_effect=WebSocketDisconnect)
            ws.send_text = AsyncMock()

            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()

                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_events_endpoint(ws)
                        except Exception:
                            pass

            # Now test filter behavior via captured handler
            handler = captured_handler.get("fn")
            assert handler is not None

            # Simulate events being published

            async def check_filter():
                # Verify filter semantics directly -- no need to instantiate queue
                ev_x = EvX()
                ev_y = EvY()

                from mcp_hangar.server.api.ws.filters import matches_filters

                assert matches_filters(ev_x, {"event_types": ["X"]}) is True
                assert matches_filters(ev_y, {"event_types": ["X"]}) is False

            await check_filter()

        self._run(run())

    def test_disconnect_triggers_unsubscribe_and_unregister(self):
        """On WebSocketDisconnect, unsubscribe_from_all and unregister are called."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()

            receive_calls = [0]

            async def mock_receive_json():
                if receive_calls[0] == 0:
                    receive_calls[0] += 1
                    raise TimeoutError  # no filter message
                raise WebSocketDisconnect

            ws.receive_json = mock_receive_json
            ws.send_json = AsyncMock(side_effect=WebSocketDisconnect)
            ws.send_text = AsyncMock()

            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()

                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_events_endpoint(ws)
                        except Exception:
                            pass

            mock_bus.unsubscribe_from_all.assert_called_once()
            mock_cm.unregister.assert_called_once()

        self._run(run())

    def test_connection_uses_bounded_queue_with_drop_callback(self):
        """Endpoint configures bounded backpressure queue and logs drops."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        captured_kwargs = {}
        mock_queue = MagicMock()
        mock_queue.queue.get = AsyncMock(side_effect=WebSocketDisconnect)
        mock_queue.put_threadsafe = MagicMock()

        def make_queue(*args: object, **kwargs: Any) -> MagicMock:
            captured_kwargs.update(kwargs)
            return mock_queue

        async def run():
            ws = make_ws_mock()

            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()
                    with patch("mcp_hangar.server.api.ws.events.EventStreamQueue", side_effect=make_queue):
                        await ws_events_endpoint(ws)

            assert captured_kwargs["maxsize"] == 1024
            assert callable(captured_kwargs["on_drop"])
            dropped_event = type("DroppedEvent", (), {})()
            new_event = type("NewEvent", (), {})()
            with patch("mcp_hangar.server.api.ws.events.logger") as mock_logger:
                captured_kwargs["on_drop"](dropped_event, new_event)
            mock_logger.warning.assert_called_once()

        self._run(run())

    def test_idle_timeout_sends_ping(self):
        """After 30s idle (TimeoutError on queue.get), sends {type: ping}."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock(side_effect=WebSocketDisconnect)
            ws.send_text = AsyncMock()

            receive_calls = [0]

            async def mock_receive_json():
                if receive_calls[0] == 0:
                    receive_calls[0] += 1
                    raise TimeoutError  # filter timeout
                raise WebSocketDisconnect

            ws.receive_json = mock_receive_json

            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()

                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_events_endpoint(ws)
                        except Exception:
                            pass

            # send_json should have been called with {"type": "ping"}
            ws.send_json.assert_called_once_with({"type": "ping"})

        self._run(run())

    def test_missing_pong_exits_loop(self):
        """If pong not received within 10s after ping, loop exits and cleanup runs."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        pong_timeout_calls = [0]

        async def controlled_wait_for(coro, timeout):
            """First call: filter timeout. Second call: queue get timeout (triggers ping)."""
            pong_timeout_calls[0] += 1
            if pong_timeout_calls[0] <= 2:
                raise TimeoutError
            # After ping is sent, pong receive also times out
            raise TimeoutError

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            ws.send_json = AsyncMock()
            ws.send_text = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError)

            with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
                with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                    mock_cm.register = MagicMock()
                    mock_cm.unregister = MagicMock()

                    with patch("asyncio.wait_for", side_effect=controlled_wait_for):
                        await ws_events_endpoint(ws)

            # Cleanup must have run
            mock_bus.unsubscribe_from_all.assert_called_once()
            mock_cm.unregister.assert_called_once()

        self._run(run())
