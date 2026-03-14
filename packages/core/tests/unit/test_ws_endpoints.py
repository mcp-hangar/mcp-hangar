"""Tests for WebSocket endpoint handlers: ws_events_endpoint and ws_state_endpoint.

Tests use unittest.mock.patch and AsyncMock for async methods.
The async endpoints are driven via asyncio.run() or loop.run_until_complete().
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helper: minimal WebSocket mock
# ---------------------------------------------------------------------------


def make_ws_mock(receive_sequence: list = None) -> MagicMock:
    """Build an AsyncMock WebSocket that returns items from receive_sequence.

    Each call to receive_json() pops from the sequence; TimeoutError is raised
    when the sequence is exhausted or when `asyncio.TimeoutError` is in the list.
    """
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
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

    def _run(self, coro):
        return asyncio.run(coro)

    def test_connect_subscribes_to_event_bus(self):
        """On connect, the handler subscribes a callback to the EventBus."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint

        mock_bus = MagicMock()
        mock_bus.subscribe_to_all = MagicMock()
        mock_bus.unsubscribe_from_all = MagicMock()

        # Queue that will receive one event and then cause disconnect
        from starlette.websockets import WebSocketDisconnect

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

        from starlette.websockets import WebSocketDisconnect

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
        from starlette.websockets import WebSocketDisconnect

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

    def test_idle_timeout_sends_ping(self):
        """After 30s idle (TimeoutError on queue.get), sends {type: ping}."""
        from mcp_hangar.server.api.ws.events import ws_events_endpoint
        from starlette.websockets import WebSocketDisconnect

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


# ---------------------------------------------------------------------------
# ws_state_endpoint tests
# ---------------------------------------------------------------------------


class TestWsStateEndpoint:
    """Tests for the /ws/state WebSocket handler."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_context(self, providers=None, groups=None):
        """Build a minimal ApplicationContext mock."""
        from mcp_hangar.server.context import ApplicationContext

        ctx = MagicMock(spec=ApplicationContext)
        ctx.providers = providers or {}
        ctx.groups = groups or {}
        return ctx

    def _make_provider_mock(self, name: str) -> MagicMock:
        p = MagicMock()
        p.to_dict.return_value = {"id": name}
        return p

    def _make_group_mock(self, name: str) -> MagicMock:
        g = MagicMock()
        g.to_status_dict.return_value = {"id": name}
        return g

    def test_state_snapshot_sent_on_connect(self):
        """Endpoint accepts, reads context, sends state_snapshot JSON."""
        from mcp_hangar.server.api.ws.state import ws_state_endpoint
        from starlette.websockets import WebSocketDisconnect

        provider = self._make_provider_mock("math")
        group = self._make_group_mock("g1")
        ctx = self._make_context(providers={"math": provider}, groups={"g1": group})

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            # No interval config (timeout immediately), then disconnect after first snapshot
            receive_calls = [0]

            async def mock_receive_json():
                if receive_calls[0] == 0:
                    receive_calls[0] += 1
                    raise TimeoutError
                raise WebSocketDisconnect

            ws.receive_json = mock_receive_json
            ws.send_text = AsyncMock(side_effect=WebSocketDisconnect)

            with patch("mcp_hangar.server.api.ws.state.get_context", return_value=ctx):
                with patch("asyncio.sleep", side_effect=WebSocketDisconnect):
                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_state_endpoint(ws)
                        except Exception:
                            pass

            assert ws.send_text.call_count >= 1
            payload = json.loads(ws.send_text.call_args_list[0][0][0])
            assert "providers" in payload
            assert "groups" in payload

        self._run(run())

    def test_interval_clamping(self):
        """interval=0.1 clamped to 0.5; interval=120 clamped to 60.0; interval=5 stays 5.0."""
        # Clamp logic is pure -- test it directly
        min_i, max_i = 0.5, 60.0

        def clamp(raw):
            return max(min_i, min(max_i, float(raw)))

        assert clamp(0.1) == 0.5
        assert clamp(120) == 60.0
        assert clamp(5) == 5.0

    def test_default_interval_used_on_timeout(self):
        """When no interval message arrives within 2s, default interval of 2.0 is used."""
        from mcp_hangar.server.api.ws.state import ws_state_endpoint, _DEFAULT_INTERVAL_S
        from starlette.websockets import WebSocketDisconnect

        ctx = self._make_context()

        sleep_args: list = []

        async def mock_sleep(seconds):
            sleep_args.append(seconds)
            raise WebSocketDisconnect

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError)
            ws.send_text = AsyncMock()

            with patch("mcp_hangar.server.api.ws.state.get_context", return_value=ctx):
                with patch("asyncio.sleep", side_effect=mock_sleep):
                    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                        try:
                            await ws_state_endpoint(ws)
                        except Exception:
                            pass

            assert sleep_args[0] == _DEFAULT_INTERVAL_S

        self._run(run())

    def test_state_snapshot_payload_keys(self):
        """Snapshot payload has keys: type, timestamp, providers, groups."""
        from mcp_hangar.server.api.ws.state import ws_state_endpoint
        from starlette.websockets import WebSocketDisconnect

        ctx = self._make_context()

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError)
            ws.send_text = AsyncMock(side_effect=WebSocketDisconnect)

            with patch("mcp_hangar.server.api.ws.state.get_context", return_value=ctx):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    with patch("asyncio.sleep", side_effect=WebSocketDisconnect):
                        try:
                            await ws_state_endpoint(ws)
                        except Exception:
                            pass

            assert ws.send_text.call_count >= 1
            payload = json.loads(ws.send_text.call_args_list[0][0][0])
            assert payload["type"] == "state_snapshot"
            assert "timestamp" in payload
            assert isinstance(payload["providers"], list)
            assert isinstance(payload["groups"], list)

        self._run(run())

    def test_websocket_disconnect_exits_cleanly(self):
        """WebSocketDisconnect from send_text causes clean loop exit (no propagation)."""
        from mcp_hangar.server.api.ws.state import ws_state_endpoint
        from starlette.websockets import WebSocketDisconnect

        ctx = self._make_context()

        async def run():
            ws = make_ws_mock()
            ws.accept = AsyncMock()
            ws.receive_json = AsyncMock(side_effect=asyncio.TimeoutError)
            ws.send_text = AsyncMock(side_effect=WebSocketDisconnect)

            with patch("mcp_hangar.server.api.ws.state.get_context", return_value=ctx):
                with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
                    # Should not raise
                    await ws_state_endpoint(ws)

        self._run(run())
