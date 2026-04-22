"""Tests for cloud.connector -- event filtering, redaction, provider snapshots, dormant mode."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mcp_hangar.cloud.config import CloudConfig
from mcp_hangar.cloud.connector import (
    CloudConnector,
    _FORWARDED_EVENT_TYPES,
    _REDACTED_KEYS,
    _redact_event_payload,
)


class _FakeProvider:
    def __init__(self, state="ready", mode="subprocess", tools=None):
        self.state = state
        self.mode = mode
        self.tools = tools or []


def _make_connector(**overrides):
    defaults = {"license_key": "test", "endpoint": "http://localhost:9999"}
    defaults.update(overrides)
    cfg = CloudConfig(**defaults)
    return CloudConnector(config=cfg, mcp_servers={})


def _make_event(name, extra_fields=None):
    event = MagicMock()
    type(event).__name__ = name
    payload = {"event_type": name}
    if extra_fields:
        payload.update(extra_fields)
    event.to_dict.return_value = payload
    return event


class TestConnectorEventFilter:
    def test_forwards_known_event_types(self):
        conn = _make_connector()
        for etype in ["ToolInvocationCompleted", "McpServerStarted", "HealthCheckFailed"]:
            conn.on_event(_make_event(etype))
        assert conn._buffer.size == 3

    def test_ignores_unknown_event_types(self):
        conn = _make_connector()
        conn.on_event(_make_event("InternalDebugEvent"))
        assert conn._buffer.size == 0

    def test_all_forwarded_types_are_strings(self):
        for t in _FORWARDED_EVENT_TYPES:
            assert isinstance(t, str)
            assert t[0].isupper()


class TestEventRedaction:
    def test_strips_arguments(self):
        payload = {
            "event_type": "ToolInvocationRequested",
            "mcp_server_id": "github",
            "tool_name": "read_file",
            "arguments": {"path": "/etc/passwd", "token": "secret123"},
        }
        redacted = _redact_event_payload(payload)
        assert "arguments" not in redacted
        assert redacted["event_type"] == "ToolInvocationRequested"
        assert redacted["mcp_server_id"] == "github"
        assert redacted["tool_name"] == "read_file"

    def test_strips_error_message(self):
        payload = {
            "event_type": "ToolInvocationFailed",
            "mcp_server_id": "slack",
            "error_message": "connection refused at /internal/secret/path",
            "error_type": "ConnectionError",
        }
        redacted = _redact_event_payload(payload)
        assert "error_message" not in redacted
        assert redacted["error_type"] == "ConnectionError"

    def test_strips_identity_context(self):
        payload = {
            "event_type": "ToolInvocationCompleted",
            "mcp_server_id": "github",
            "identity_context": {"user_id": "u123", "email": "a@b.com"},
            "duration_ms": 42.0,
        }
        redacted = _redact_event_payload(payload)
        assert "identity_context" not in redacted
        assert redacted["duration_ms"] == 42.0

    def test_preserves_safe_fields(self):
        payload = {
            "event_type": "McpServerStarted",
            "mcp_server_id": "math",
            "mode": "subprocess",
            "tools_count": 5,
        }
        assert _redact_event_payload(payload) == payload

    def test_redaction_applied_in_on_event(self):
        conn = _make_connector()
        event = _make_event("ToolInvocationRequested", {
            "arguments": {"password": "hunter2"},
            "identity_context": {"user": "root"},
            "mcp_server_id": "test",
        })
        conn.on_event(event)
        buffered = conn._buffer.drain(1)[0]
        assert "arguments" not in buffered
        assert "identity_context" not in buffered
        assert buffered["mcp_server_id"] == "test"

    def test_redacted_keys_constant_covers_sensitive_fields(self):
        assert "arguments" in _REDACTED_KEYS
        assert "error_message" in _REDACTED_KEYS
        assert "identity_context" in _REDACTED_KEYS


class TestConnectorProviderSnapshots:
    def test_build_snapshots(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        providers = {
            "github": _FakeProvider(state="ready", mode="subprocess", tools=[]),
            "slack": _FakeProvider(state="dead", mode="docker", tools=[]),
        }
        conn = CloudConnector(config=cfg, mcp_servers=providers)
        snaps = conn._build_mcp_server_snapshots()
        assert len(snaps) == 2
        github = next(s for s in snaps if s["id"] == "github")
        assert github["status"] == "READY"
        assert github["health"] == "healthy"
        slack = next(s for s in snaps if s["id"] == "slack")
        assert slack["status"] == "DEAD"
        assert slack["health"] == "unhealthy"

    def test_mcp_server_counts(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        providers = {
            "a": _FakeProvider(state="ready"),
            "b": _FakeProvider(state="ready"),
            "c": _FakeProvider(state="dead"),
        }
        conn = CloudConnector(config=cfg, mcp_servers=providers)
        total, healthy = conn._mcp_server_counts()
        assert total == 3
        assert healthy == 2

    def test_empty_providers(self):
        cfg = CloudConfig(license_key="test", endpoint="http://localhost:9999")
        conn = CloudConnector(config=cfg, mcp_servers={})
        assert conn._build_mcp_server_snapshots() == []
        assert conn._mcp_server_counts() == (0, 0)


class TestDormantMode:
    def test_initial_state_not_dormant(self):
        conn = _make_connector()
        assert conn.dormant is False
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_register_returns_false_after_max_attempts(self):
        cfg = CloudConfig(
            license_key="test",
            endpoint="http://localhost:9999",
            max_registration_attempts=3,
        )
        conn = CloudConnector(config=cfg, mcp_servers={})
        conn._stop_event = asyncio.Event()

        client = MagicMock()
        client.register = AsyncMock(side_effect=OSError("refused"))

        result = await conn._register_with_retry(client)
        assert result is False
        assert client.register.call_count == 3
        assert conn.connected is False

    @pytest.mark.asyncio
    async def test_register_succeeds_on_second_attempt(self):
        cfg = CloudConfig(
            license_key="test",
            endpoint="http://localhost:9999",
            max_registration_attempts=5,
        )
        conn = CloudConnector(config=cfg, mcp_servers={})
        conn._stop_event = asyncio.Event()

        client = MagicMock()
        client.register = AsyncMock(side_effect=[OSError("refused"), {"agent_id": "a1"}])

        result = await conn._register_with_retry(client)
        assert result is True
        assert client.register.call_count == 2
        assert conn.connected is True

    @pytest.mark.asyncio
    async def test_register_respects_stop_event(self):
        cfg = CloudConfig(
            license_key="test",
            endpoint="http://localhost:9999",
            max_registration_attempts=100,
        )
        conn = CloudConnector(config=cfg, mcp_servers={})
        conn._stop_event = asyncio.Event()
        conn._stop_event.set()

        client = MagicMock()
        client.register = AsyncMock(side_effect=OSError("refused"))

        result = await conn._register_with_retry(client)
        assert result is False
