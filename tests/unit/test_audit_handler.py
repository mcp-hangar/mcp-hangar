"""Tests for Audit Event Handler."""

from datetime import datetime, UTC
from unittest.mock import patch

import pytest

from mcp_hangar.application.event_handlers.audit_handler import (
    AuditEventHandler,
    AuditRecord,
    AuditStore,
    InMemoryAuditStore,
    LogAuditStore,
)
from mcp_hangar.domain.events import (
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)


class TestAuditRecord:
    """Test AuditRecord dataclass."""

    def test_audit_record_creation(self):
        """Test creating an audit record."""
        now = datetime.now(UTC)
        record = AuditRecord(
            event_id="evt-123",
            event_type="ProviderStarted",
            occurred_at=now,
            provider_id="test-provider",
            data={"mode": "subprocess"},
        )

        assert record.event_id == "evt-123"
        assert record.event_type == "ProviderStarted"
        assert record.occurred_at == now
        assert record.provider_id == "test-provider"
        assert record.data == {"mode": "subprocess"}
        assert isinstance(record.recorded_at, datetime)

    def test_audit_record_to_dict(self):
        """Test audit record to dictionary conversion."""
        now = datetime.now(UTC)
        record = AuditRecord(
            event_id="evt-456",
            event_type="ProviderStopped",
            occurred_at=now,
            provider_id="test",
            data={"reason": "idle"},
        )

        d = record.to_dict()

        assert d["event_id"] == "evt-456"
        assert d["event_type"] == "ProviderStopped"
        assert "occurred_at" in d
        assert d["provider_id"] == "test"
        assert d["data"] == {"reason": "idle"}
        assert "recorded_at" in d

    def test_audit_record_to_json(self):
        """Test audit record to JSON conversion."""
        now = datetime.now(UTC)
        record = AuditRecord(
            event_id="evt-789",
            event_type="Test",
            occurred_at=now,
            provider_id="p1",
            data={},
        )

        json_str = record.to_json()

        assert "evt-789" in json_str
        assert "Test" in json_str
        assert "p1" in json_str


class TestInMemoryAuditStore:
    """Test InMemoryAuditStore implementation."""

    def test_record_audit_entry(self):
        """Test recording an audit entry."""
        store = InMemoryAuditStore()

        record = AuditRecord(
            event_id="evt-1",
            event_type="ProviderStarted",
            occurred_at=datetime.now(UTC),
            provider_id="p1",
            data={},
        )

        store.record(record)

        assert store.count == 1

    def test_query_all_records(self):
        """Test querying all records."""
        store = InMemoryAuditStore()

        record1 = AuditRecord("evt-1", "ProviderStarted", datetime.now(UTC), "p1", {})
        record2 = AuditRecord("evt-2", "ProviderStopped", datetime.now(UTC), "p1", {})

        store.record(record1)
        store.record(record2)

        records = store.query()

        # Returns most recent first
        assert len(records) == 2

    def test_query_by_provider(self):
        """Test querying records by provider ID."""
        store = InMemoryAuditStore()

        store.record(AuditRecord("e1", "ProviderStarted", datetime.now(UTC), "p1", {}))
        store.record(AuditRecord("e2", "ProviderStarted", datetime.now(UTC), "p2", {}))
        store.record(AuditRecord("e3", "ProviderStopped", datetime.now(UTC), "p1", {}))

        p1_records = store.query(provider_id="p1")
        p2_records = store.query(provider_id="p2")

        assert len(p1_records) == 2
        assert len(p2_records) == 1

    def test_query_by_event_type(self):
        """Test querying records by event type."""
        store = InMemoryAuditStore()

        store.record(AuditRecord("e1", "ProviderStarted", datetime.now(UTC), "p1", {}))
        store.record(AuditRecord("e2", "ProviderStarted", datetime.now(UTC), "p2", {}))
        store.record(AuditRecord("e3", "ProviderStopped", datetime.now(UTC), "p1", {}))

        started_records = store.query(event_type="ProviderStarted")
        stopped_records = store.query(event_type="ProviderStopped")

        assert len(started_records) == 2
        assert len(stopped_records) == 1

    def test_query_with_limit(self):
        """Test querying with limit."""
        store = InMemoryAuditStore()

        for i in range(10):
            store.record(AuditRecord(f"e{i}", "Event", datetime.now(UTC), "p1", {}))

        records = store.query(limit=5)

        assert len(records) == 5

    def test_clear_records(self):
        """Test clearing all records."""
        store = InMemoryAuditStore()

        store.record(AuditRecord("e1", "Event", datetime.now(UTC), "p1", {}))
        store.record(AuditRecord("e2", "Event", datetime.now(UTC), "p1", {}))

        store.clear()

        assert store.count == 0

    def test_max_records_limit(self):
        """Test that store respects max records limit."""
        store = InMemoryAuditStore(max_records=3)

        for i in range(5):
            store.record(AuditRecord(f"e{i}", "Event", datetime.now(UTC), "p1", {}))

        # Should only keep last 3 records
        assert store.count == 3
        records = store.query()
        event_ids = [r.event_id for r in records]
        assert "e2" in event_ids
        assert "e3" in event_ids
        assert "e4" in event_ids

    def test_query_returns_most_recent_first(self):
        """Test query returns records in reverse chronological order."""
        store = InMemoryAuditStore()

        store.record(AuditRecord("e1", "Event", datetime.now(UTC), "p1", {}))
        store.record(AuditRecord("e2", "Event", datetime.now(UTC), "p1", {}))
        store.record(AuditRecord("e3", "Event", datetime.now(UTC), "p1", {}))

        records = store.query()

        assert records[0].event_id == "e3"
        assert records[1].event_id == "e2"
        assert records[2].event_id == "e1"


class TestLogAuditStore:
    """Test LogAuditStore implementation."""

    def test_log_store_logs_record(self):
        """Test that LogAuditStore logs records."""
        store = LogAuditStore()

        record = AuditRecord(
            event_id="evt-1",
            event_type="ProviderStarted",
            occurred_at=datetime.now(UTC),
            provider_id="test",
            data={"mode": "subprocess"},
        )

        with patch.object(store._logger, "info") as mock_info:
            store.record(record)
            mock_info.assert_called()

    def test_log_store_query_not_supported(self):
        """Test LogAuditStore doesn't support queries."""
        store = LogAuditStore()

        with pytest.raises(NotImplementedError):
            store.query()


class TestAuditEventHandler:
    """Test AuditEventHandler."""

    def test_handler_with_default_store(self):
        """Test handler uses InMemoryAuditStore by default."""
        handler = AuditEventHandler()

        assert isinstance(handler._store, InMemoryAuditStore)

    def test_handler_with_custom_store(self):
        """Test handler with custom store."""
        custom_store = InMemoryAuditStore()
        handler = AuditEventHandler(store=custom_store)

        assert handler._store is custom_store

    def test_handle_provider_started_event(self):
        """Test handling ProviderStarted event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ProviderStarted(
            provider_id="test-provider",
            mode="subprocess",
            tools_count=5,
            startup_duration_ms=150.0,
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].event_type == "ProviderStarted"
        assert records[0].provider_id == "test-provider"

    def test_handle_provider_stopped_event(self):
        """Test handling ProviderStopped event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ProviderStopped(provider_id="test-provider", reason="idle")

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].event_type == "ProviderStopped"
        assert "reason" in records[0].data

    def test_handle_provider_state_changed_event(self):
        """Test handling ProviderStateChanged event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ProviderStateChanged(provider_id="test", old_state="cold", new_state="ready")

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["old_state"] == "cold"
        assert records[0].data["new_state"] == "ready"

    def test_handle_tool_invocation_requested_event(self):
        """Test handling ToolInvocationRequested event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ToolInvocationRequested(
            provider_id="test",
            tool_name="add",
            correlation_id="corr-123",
            arguments={"a": 1, "b": 2},
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["tool_name"] == "add"

    def test_handle_tool_invocation_completed_event(self):
        """Test handling ToolInvocationCompleted event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ToolInvocationCompleted(
            provider_id="test",
            tool_name="add",
            correlation_id="corr-123",
            duration_ms=150.0,
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["duration_ms"] == 150.0

    def test_handle_tool_invocation_failed_event(self):
        """Test handling ToolInvocationFailed event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ToolInvocationFailed(
            provider_id="test",
            tool_name="add",
            correlation_id="corr-123",
            error_message="timeout",
            error_type="TimeoutError",
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["error_message"] == "timeout"

    def test_handle_provider_degraded_event(self):
        """Test handling ProviderDegraded event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = ProviderDegraded(
            provider_id="test",
            consecutive_failures=5,
            total_failures=10,
            reason="timeout",
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["consecutive_failures"] == 5

    def test_handle_health_check_passed_event(self):
        """Test handling HealthCheckPassed event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = HealthCheckPassed(provider_id="test", duration_ms=50.0)

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].event_type == "HealthCheckPassed"

    def test_handle_health_check_failed_event(self):
        """Test handling HealthCheckFailed event."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        event = HealthCheckFailed(
            provider_id="test",
            consecutive_failures=3,
            error_message="connection refused",
        )

        handler.handle(event)

        records = store.query()
        assert len(records) == 1
        assert records[0].data["error_message"] == "connection refused"

    def test_records_have_unique_event_ids(self):
        """Test that each record uses the event's ID."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        for i in range(5):
            event = ProviderStarted(
                provider_id=f"p{i}",
                mode="subprocess",
                tools_count=1,
                startup_duration_ms=100.0,
            )
            handler.handle(event)

        records = store.query()
        event_ids = [r.event_id for r in records]

        # All IDs should be unique
        assert len(event_ids) == len(set(event_ids))

    def test_records_have_accurate_timestamps(self):
        """Test that records have accurate timestamps."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        before = datetime.now(UTC)
        event = ProviderStarted(
            provider_id="test",
            mode="subprocess",
            tools_count=1,
            startup_duration_ms=100.0,
        )
        handler.handle(event)
        after = datetime.now(UTC)

        records = store.query()
        assert before <= records[0].recorded_at <= after

    def test_multiple_events_all_recorded(self):
        """Test multiple events are all recorded."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store)

        handler.handle(ProviderStarted("p1", "subprocess", 1, 100.0))
        handler.handle(ProviderStopped("p1", "idle"))
        handler.handle(ProviderStarted("p2", "docker", 2, 200.0))

        records = store.query()
        assert len(records) == 3

    def test_query_method(self):
        """Test handler provides access to records via query."""
        handler = AuditEventHandler()

        handler.handle(ProviderStarted("test", "subprocess", 1, 100.0))

        records = handler.query()
        assert len(records) == 1

    def test_query_by_provider(self):
        """Test querying records filtered by provider."""
        handler = AuditEventHandler()

        handler.handle(ProviderStarted("p1", "subprocess", 1, 100.0))
        handler.handle(ProviderStarted("p2", "subprocess", 1, 100.0))
        handler.handle(ProviderStopped("p1", "idle"))

        p1_records = handler.query(provider_id="p1")
        assert len(p1_records) == 2

    def test_include_event_types_filter(self):
        """Test filtering by included event types."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store, include_event_types=["ProviderStarted", "ProviderStopped"])

        handler.handle(ProviderStarted("p1", "subprocess", 1, 100.0))
        handler.handle(ProviderStopped("p1", "idle"))
        handler.handle(ProviderDegraded("p1", 3, 5, "error"))  # Should be excluded

        records = store.query()
        assert len(records) == 2

    def test_exclude_event_types_filter(self):
        """Test filtering by excluded event types."""
        store = InMemoryAuditStore()
        handler = AuditEventHandler(store=store, exclude_event_types=["HealthCheckPassed"])

        handler.handle(ProviderStarted("p1", "subprocess", 1, 100.0))
        handler.handle(HealthCheckPassed("p1", 50.0))  # Should be excluded

        records = store.query()
        assert len(records) == 1
        assert records[0].event_type == "ProviderStarted"


class TestAuditStoreInterface:
    """Test AuditStore abstract interface."""

    def test_store_requires_methods(self):
        """Test AuditStore requires all methods."""
        with pytest.raises(TypeError):

            class IncompleteStore(AuditStore):
                pass

            IncompleteStore()

    def test_custom_store_implementation(self):
        """Test custom store implementation."""

        class ListStore(AuditStore):
            def __init__(self):
                self.records = []

            def record(self, audit_record: AuditRecord) -> None:
                self.records.append(audit_record)

            def query(self, provider_id=None, event_type=None, since=None, limit=100):
                results = self.records.copy()
                if provider_id:
                    results = [r for r in results if r.provider_id == provider_id]
                if event_type:
                    results = [r for r in results if r.event_type == event_type]
                return results[:limit]

        store = ListStore()
        record = AuditRecord("e1", "Event", datetime.now(UTC), "p1", {})
        store.record(record)

        assert len(store.query()) == 1
