"""`/ws/events`: the ack reflects the subscription, and no early event is lost (#1704).

`ToolApproval*` is not a pattern the matcher supports (a wildcard is a whole
segment), so it matched nothing, yet the `subscribed` ack echoed it. Events
published before the client's subscribe message were lost because the bus
handler was only registered after it.
"""

from __future__ import annotations

import time

import pytest
from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient

from mcp_hangar.domain.events import ToolInvocationCompleted
from mcp_hangar.infrastructure.event_bus import get_event_bus, reset_event_bus
from mcp_hangar.server.api.ws.events import ws_events_endpoint
from mcp_hangar.server.api.ws.filters import rejected_event_patterns
from mcp_hangar.server.api.ws.manager import connection_manager


@pytest.fixture(autouse=True)
def _fresh_bus():
    reset_event_bus()
    yield
    reset_event_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(Starlette(routes=[WebSocketRoute("/ws/events", ws_events_endpoint)]))


def _completed(server: str) -> ToolInvocationCompleted:
    return ToolInvocationCompleted(mcp_server_id=server, tool_name="add", correlation_id="c")


def _wait_registered(before: int) -> None:
    deadline = time.monotonic() + 5
    while connection_manager.active_count <= before and time.monotonic() < deadline:
        time.sleep(0.01)


def test_rejected_patterns_are_the_ones_the_matcher_drops():
    assert rejected_event_patterns(["ToolApproval*", "ToolInvocationCompleted", "tools/*", "a/b*", ""]) == [
        "ToolApproval*",
        "a/b*",
        "",
    ]


def test_the_ack_names_a_partial_wildcard_as_rejected_and_not_as_subscribed(client):
    with client.websocket_connect("/ws/events") as ws:
        ws.send_json({"type": "subscribe", "event_types": ["ToolApproval*", "ToolInvocationCompleted"]})
        ack = ws.receive_json()
    assert ack["type"] == "subscribed"
    assert ack["event_types"] == ["ToolInvocationCompleted"]
    assert ack["rejected_event_types"] == ["ToolApproval*"]


def test_the_ack_of_a_clean_subscription_rejects_nothing(client):
    with client.websocket_connect("/ws/events") as ws:
        ws.send_json({"type": "subscribe", "event_types": ["*"]})
        ack = ws.receive_json()
    assert ack["event_types"] == ["*"]
    assert ack["rejected_event_types"] == []


def test_an_event_published_before_the_subscribe_message_is_delivered(client):
    before = connection_manager.active_count
    with client.websocket_connect("/ws/events") as ws:
        _wait_registered(before)
        get_event_bus().publish(_completed("early"))
        ws.send_json({"type": "subscribe"})
        assert ws.receive_json()["type"] == "subscribed"
        assert ws.receive_json()["mcp_server_id"] == "early"


def test_an_early_event_the_subscription_filters_out_is_not_delivered(client):
    before = connection_manager.active_count
    with client.websocket_connect("/ws/events") as ws:
        _wait_registered(before)
        get_event_bus().publish(_completed("early-other"))
        ws.send_json({"type": "subscribe", "mcp_server_ids": ["wanted"]})
        assert ws.receive_json()["type"] == "subscribed"
        get_event_bus().publish(_completed("wanted"))
        assert ws.receive_json()["mcp_server_id"] == "wanted"
