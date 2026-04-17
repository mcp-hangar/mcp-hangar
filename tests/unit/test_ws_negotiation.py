"""Tests for websocket subscription negotiation before event streaming."""

# pyright: reportAny=false

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.websockets import WebSocketDisconnect

from mcp_hangar.server.api.ws.filters import parse_subscription_filters


def _make_ws() -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_text = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()
    ws.headers = {}
    ws.scope = {}
    ws.url = MagicMock(path="/ws/events", query="")
    return ws


def _make_event(event_type: str, provider_id: str | None = None) -> MagicMock:
    event = MagicMock()
    payload = {"event_type": event_type}
    if provider_id is not None:
        payload["provider_id"] = provider_id
    event.to_dict.return_value = payload
    return event


def test_parse_subscription_filters_accepts_subscribe_messages() -> None:
    """Subscribe control messages should parse into normal filter config."""
    result = parse_subscription_filters(
        {"type": "subscribe", "event_types": ["ProviderStarted"], "provider_ids": ["math"]}
    )

    assert result == {"event_types": ["ProviderStarted"], "provider_ids": ["math"]}


def test_ws_events_negotiates_subscription_before_streaming() -> None:
    """Initial subscribe message updates filters and receives an acknowledgment."""
    from mcp_hangar.server.api.ws.events import ws_events_endpoint

    captured_handler: dict[str, Callable[[MagicMock], None]] = {}

    def subscribe_side_effect(handler: Callable[[MagicMock], None]) -> None:
        captured_handler["fn"] = handler

    mock_bus = MagicMock()
    mock_bus.subscribe_to_all = MagicMock(side_effect=subscribe_side_effect)
    mock_bus.unsubscribe_from_all = MagicMock()

    mock_queue = MagicMock()
    mock_queue.queue.get = AsyncMock(side_effect=WebSocketDisconnect)
    mock_queue.put_threadsafe = MagicMock()

    async def run() -> None:
        ws = _make_ws()
        ws.receive_json = AsyncMock(
            side_effect=[
                {"type": "subscribe", "event_types": ["ProviderStarted"], "provider_ids": ["math"]},
                WebSocketDisconnect(),
            ]
        )

        with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
            with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                mock_cm.register = MagicMock()
                mock_cm.unregister = MagicMock()
                with patch("mcp_hangar.server.api.ws.events.EventStreamQueue", return_value=mock_queue):
                    await ws_events_endpoint(ws)

        ws.send_json.assert_awaited_once_with(
            {"type": "subscribed", "event_types": ["ProviderStarted"], "provider_ids": ["math"]}
        )

    asyncio.run(run())

    handler = captured_handler["fn"]
    matching_event = _make_event("ProviderStarted", "math")
    non_matching_event = _make_event("ProviderStopped", "math")

    handler(matching_event)
    handler(non_matching_event)

    mock_queue.put_threadsafe.assert_called_once()
    assert mock_queue.put_threadsafe.call_args.args[0] is matching_event


def test_ws_events_negotiation_timeout_falls_back_to_unfiltered_stream() -> None:
    """Missing initial subscribe message should keep backward-compatible all-events behavior."""
    from mcp_hangar.server.api.ws.events import ws_events_endpoint

    captured_handler: dict[str, Callable[[MagicMock], None]] = {}

    def subscribe_side_effect(handler: Callable[[MagicMock], None]) -> None:
        captured_handler["fn"] = handler

    mock_bus = MagicMock()
    mock_bus.subscribe_to_all = MagicMock(side_effect=subscribe_side_effect)
    mock_bus.unsubscribe_from_all = MagicMock()

    mock_queue = MagicMock()
    mock_queue.queue.get = AsyncMock(side_effect=WebSocketDisconnect)
    mock_queue.put_threadsafe = MagicMock()

    async def run() -> None:
        ws = _make_ws()
        ws.receive_json = AsyncMock(side_effect=[asyncio.TimeoutError(), WebSocketDisconnect()])

        with patch("mcp_hangar.server.api.ws.events.get_event_bus", return_value=mock_bus):
            with patch("mcp_hangar.server.api.ws.events.connection_manager") as mock_cm:
                mock_cm.register = MagicMock()
                mock_cm.unregister = MagicMock()
                with patch("mcp_hangar.server.api.ws.events.EventStreamQueue", return_value=mock_queue):
                    await ws_events_endpoint(ws)

        ws.send_json.assert_not_awaited()

    asyncio.run(run())

    handler = captured_handler["fn"]
    event = _make_event("AnythingAtAll", "provider-1")
    handler(event)

    mock_queue.put_threadsafe.assert_called_once()
    assert mock_queue.put_threadsafe.call_args.args[0] is event
