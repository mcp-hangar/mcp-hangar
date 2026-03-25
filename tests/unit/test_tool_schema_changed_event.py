"""Tests for ToolSchemaChanged event, SchemaChangeType enum, and ToolSchemaChangeHandler.

Covers:
- SchemaChangeType enum values and __str__
- ToolSchemaChanged event creation (added, removed) and serialization (to_dict)
- ToolSchemaChangeHandler:
  - Ignores non-ProviderStarted events
  - No-op when schema_tracker is None
  - SC45-4: First-seen produces no ToolSchemaChanged events
  - SC45-1: ADDED tool publishes ToolSchemaChanged(added)
  - SC45-2: REMOVED tool publishes ToolSchemaChanged(removed)
  - SC45-3: MODIFIED tool publishes ToolSchemaChanged(modified) with different hashes
  - Error isolation: SchemaTracker errors logged, not propagated
  - Prometheus counter incremented on each change
"""

from unittest.mock import MagicMock, patch

from enterprise.behavioral.schema_tracker import SchemaTracker
from mcp_hangar.application.event_handlers.tool_schema_change_handler import (
    ToolSchemaChangeHandler,
)
from mcp_hangar.domain.events import (
    ProviderStarted,
    ProviderStopped,
    ToolSchemaChanged,
)
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.value_objects.behavioral import SchemaChangeType


def _tool(name: str, input_schema: dict | None = None) -> ToolSchema:
    """Helper to create a ToolSchema with sensible defaults."""
    return ToolSchema(
        name=name,
        description=f"Test {name}",
        input_schema=input_schema or {"type": "object", "properties": {}},
    )


def _make_provider_mock(tools: list[ToolSchema]) -> MagicMock:
    """Create a mock provider that returns the given tools from get_tool_schemas."""
    mock = MagicMock()
    mock.get_tool_schemas.return_value = tools
    return mock


# ---------------------------------------------------------------------------
# SchemaChangeType enum
# ---------------------------------------------------------------------------


class TestSchemaChangeType:
    """Tests for the SchemaChangeType enum."""

    def test_values(self) -> None:
        """ADDED/REMOVED/MODIFIED have correct string values."""
        assert SchemaChangeType.ADDED.value == "added"
        assert SchemaChangeType.REMOVED.value == "removed"
        assert SchemaChangeType.MODIFIED.value == "modified"

    def test_str(self) -> None:
        """str(SchemaChangeType.ADDED) returns the value string."""
        assert str(SchemaChangeType.ADDED) == "added"
        assert str(SchemaChangeType.REMOVED) == "removed"
        assert str(SchemaChangeType.MODIFIED) == "modified"


# ---------------------------------------------------------------------------
# ToolSchemaChanged event
# ---------------------------------------------------------------------------


class TestToolSchemaChangedEvent:
    """Tests for the ToolSchemaChanged domain event."""

    def test_added_event(self) -> None:
        """Create ADDED event with old_hash=None, verify fields."""
        event = ToolSchemaChanged(
            provider_id="math",
            tool_name="multiply",
            change_type="added",
            old_hash=None,
            new_hash="abc123",
        )
        assert event.provider_id == "math"
        assert event.tool_name == "multiply"
        assert event.change_type == "added"
        assert event.old_hash is None
        assert event.new_hash == "abc123"
        assert event.schema_version == 1

    def test_removed_event(self) -> None:
        """Create REMOVED event with new_hash=None, verify fields."""
        event = ToolSchemaChanged(
            provider_id="math",
            tool_name="subtract",
            change_type="removed",
            old_hash="def456",
            new_hash=None,
        )
        assert event.provider_id == "math"
        assert event.tool_name == "subtract"
        assert event.change_type == "removed"
        assert event.old_hash == "def456"
        assert event.new_hash is None

    def test_to_dict(self) -> None:
        """to_dict includes event_type and all fields."""
        event = ToolSchemaChanged(
            provider_id="math",
            tool_name="add",
            change_type="modified",
            old_hash="aaa",
            new_hash="bbb",
        )
        d = event.to_dict()
        assert d["event_type"] == "ToolSchemaChanged"
        assert d["provider_id"] == "math"
        assert d["tool_name"] == "add"
        assert d["change_type"] == "modified"
        assert d["old_hash"] == "aaa"
        assert d["new_hash"] == "bbb"
        assert "event_id" in d
        assert "occurred_at" in d


# ---------------------------------------------------------------------------
# ToolSchemaChangeHandler
# ---------------------------------------------------------------------------


class TestToolSchemaChangeHandler:
    """Tests for the ToolSchemaChangeHandler event handler."""

    def test_ignores_non_provider_started(self) -> None:
        """Passing a non-ProviderStarted event causes no error and no publish."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()
        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={},
            event_bus=event_bus,
        )

        stopped_event = ProviderStopped(provider_id="math", reason="shutdown")
        handler.handle(stopped_event)

        event_bus.publish.assert_not_called()

    def test_noop_when_schema_tracker_none(self) -> None:
        """When schema_tracker is None, event_bus.publish is never called."""
        event_bus = MagicMock()
        handler = ToolSchemaChangeHandler(
            schema_tracker=None,
            providers={},
            event_bus=event_bus,
        )

        started_event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=2,
            startup_duration_ms=100.0,
        )
        handler.handle(started_event)

        event_bus.publish.assert_not_called()

    def test_first_seen_no_events(self) -> None:
        """SC45-4: First startup for a provider produces no ToolSchemaChanged events."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()
        tools = [_tool("add"), _tool("subtract")]
        provider = _make_provider_mock(tools)

        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        started_event = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=2,
            startup_duration_ms=100.0,
        )
        handler.handle(started_event)

        event_bus.publish.assert_not_called()

    def test_added_tool_emits_event(self) -> None:
        """SC45-1: New tool on second startup publishes ToolSchemaChanged(added)."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()

        tools_v1 = [_tool("add")]
        tools_v2 = [_tool("add"), _tool("multiply")]
        provider = _make_provider_mock(tools_v1)

        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        # First startup: baseline
        started = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=100.0,
        )
        handler.handle(started)
        event_bus.publish.assert_not_called()

        # Second startup: add multiply
        provider.get_tool_schemas.return_value = tools_v2
        started2 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=2,
            startup_duration_ms=80.0,
        )
        handler.handle(started2)

        assert event_bus.publish.call_count == 1
        published_event = event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ToolSchemaChanged)
        assert published_event.tool_name == "multiply"
        assert published_event.change_type == "added"
        assert published_event.old_hash is None
        assert published_event.new_hash is not None

    def test_removed_tool_emits_event(self) -> None:
        """SC45-2: Removed tool on second startup publishes ToolSchemaChanged(removed)."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()

        tools_v1 = [_tool("add"), _tool("subtract")]
        tools_v2 = [_tool("add")]
        provider = _make_provider_mock(tools_v1)

        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        # First startup
        started = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=2,
            startup_duration_ms=100.0,
        )
        handler.handle(started)

        # Second startup: remove subtract
        provider.get_tool_schemas.return_value = tools_v2
        started2 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=80.0,
        )
        handler.handle(started2)

        assert event_bus.publish.call_count == 1
        published_event = event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ToolSchemaChanged)
        assert published_event.tool_name == "subtract"
        assert published_event.change_type == "removed"
        assert published_event.old_hash is not None
        assert published_event.new_hash is None

    def test_modified_tool_emits_event(self) -> None:
        """SC45-3: Modified schema on second startup publishes ToolSchemaChanged(modified)."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()

        schema_v1 = {"type": "object", "properties": {"x": {"type": "integer"}}}
        schema_v2 = {"type": "object", "properties": {"x": {"type": "string"}, "y": {"type": "integer"}}}
        tools_v1 = [_tool("add", schema_v1)]
        tools_v2 = [_tool("add", schema_v2)]
        provider = _make_provider_mock(tools_v1)

        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        # First startup
        started = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=100.0,
        )
        handler.handle(started)

        # Second startup: modify schema
        provider.get_tool_schemas.return_value = tools_v2
        started2 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=80.0,
        )
        handler.handle(started2)

        assert event_bus.publish.call_count == 1
        published_event = event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ToolSchemaChanged)
        assert published_event.tool_name == "add"
        assert published_event.change_type == "modified"
        assert published_event.old_hash is not None
        assert published_event.new_hash is not None
        assert published_event.old_hash != published_event.new_hash

    def test_handler_error_does_not_propagate(self) -> None:
        """SchemaTracker raising RuntimeError does not propagate; publish not called."""
        broken_tracker = MagicMock()
        broken_tracker.check_and_store.side_effect = RuntimeError("DB failure")
        event_bus = MagicMock()
        provider = _make_provider_mock([_tool("add")])

        handler = ToolSchemaChangeHandler(
            schema_tracker=broken_tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        started = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=100.0,
        )
        # Should not raise
        handler.handle(started)

        event_bus.publish.assert_not_called()

    @patch("mcp_hangar.application.event_handlers.tool_schema_change_handler.prometheus_metrics")
    def test_prometheus_counter_incremented(self, mock_metrics: MagicMock) -> None:
        """Prometheus record_tool_schema_drift called for each detected change."""
        tracker = SchemaTracker(":memory:")
        event_bus = MagicMock()

        tools_v1 = [_tool("add")]
        tools_v2 = [_tool("add"), _tool("multiply")]
        provider = _make_provider_mock(tools_v1)

        handler = ToolSchemaChangeHandler(
            schema_tracker=tracker,
            providers={"math": provider},
            event_bus=event_bus,
        )

        # First startup: baseline
        started = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=100.0,
        )
        handler.handle(started)
        mock_metrics.record_tool_schema_drift.assert_not_called()

        # Second startup: add multiply
        provider.get_tool_schemas.return_value = tools_v2
        started2 = ProviderStarted(
            provider_id="math",
            mode="subprocess",
            tools_count=2,
            startup_duration_ms=80.0,
        )
        handler.handle(started2)

        mock_metrics.record_tool_schema_drift.assert_called_once_with(
            provider="math",
            change_type="added",
        )
