"""Tests for task-lifecycle audit events (#321).

Verifies the five async task-lifecycle events are defined carrying
tenant_id + task_id + correlation_id, are recorded by the audit trail when
published on the event bus, and that the full trail is reconstructable per
task_id.
"""

from mcp_hangar.application.event_handlers.audit_handler import (
    AuditEventHandler,
    InMemoryAuditStore,
)
from mcp_hangar.domain.events import (
    TaskCancelled,
    TaskCompleted,
    TaskCreated,
    TaskFailed,
    TaskInputRequired,
)
from mcp_hangar.infrastructure.event_bus import EventBus

TASK_EVENT_CLASSES = [
    TaskCreated,
    TaskInputRequired,
    TaskCompleted,
    TaskFailed,
    TaskCancelled,
]


class TestTaskLifecycleEvents:
    """The five events carry the required audit keys."""

    def test_all_five_events_carry_audit_keys(self):
        for cls in TASK_EVENT_CLASSES:
            event = cls(
                task_id="task-1",
                tenant_id="tenant-a",
                correlation_id="corr-1",
            )
            assert event.task_id == "task-1"
            assert event.tenant_id == "tenant-a"
            assert event.correlation_id == "corr-1"
            # Base DomainEvent fields are populated.
            assert event.event_id
            assert event.occurred_at > 0

    def test_to_dict_serializes_audit_keys(self):
        for cls in TASK_EVENT_CLASSES:
            event = cls(task_id="t", tenant_id="ten", correlation_id="c")
            d = event.to_dict()
            assert d["event_type"] == cls.__name__
            assert d["task_id"] == "t"
            assert d["tenant_id"] == "ten"
            assert d["correlation_id"] == "c"

    def test_tenant_id_optional(self):
        # tenant_id may be absent in single-tenant deployments.
        event = TaskCreated(task_id="t", correlation_id="c")
        assert event.tenant_id is None

    def test_event_specific_fields(self):
        created = TaskCreated(task_id="t", mcp_server_id="srv", tool_name="run")
        assert created.mcp_server_id == "srv"
        assert created.tool_name == "run"

        input_required = TaskInputRequired(task_id="t", message="need approval")
        assert input_required.message == "need approval"

        completed = TaskCompleted(task_id="t", duration_ms=42.0)
        assert completed.duration_ms == 42.0

        failed = TaskFailed(task_id="t", error_type="TimeoutError", error_message="boom")
        assert failed.error_type == "TimeoutError"
        assert failed.error_message == "boom"

        cancelled = TaskCancelled(task_id="t", reason="user", cancelled_by="alice")
        assert cancelled.reason == "user"
        assert cancelled.cancelled_by == "alice"


class TestTaskLifecycleAuditTrail:
    """The audit handler records all five events and rebuilds the trail."""

    def _handler(self) -> AuditEventHandler:
        return AuditEventHandler(store=InMemoryAuditStore())

    def _emit_full_lifecycle(self, bus: EventBus, task_id: str, tenant_id: str) -> None:
        bus.publish(
            TaskCreated(
                task_id=task_id,
                tenant_id=tenant_id,
                correlation_id="corr",
                mcp_server_id="srv",
                tool_name="long_job",
            )
        )
        bus.publish(TaskInputRequired(task_id=task_id, tenant_id=tenant_id, correlation_id="corr"))
        bus.publish(TaskCompleted(task_id=task_id, tenant_id=tenant_id, correlation_id="corr", duration_ms=10.0))
        bus.publish(TaskFailed(task_id=task_id, tenant_id=tenant_id, correlation_id="corr", error_type="E"))
        bus.publish(TaskCancelled(task_id=task_id, tenant_id=tenant_id, correlation_id="corr", reason="done"))

    def test_all_five_events_recorded(self):
        handler = self._handler()
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        self._emit_full_lifecycle(bus, "task-42", "tenant-a")

        records = handler.query(task_id="task-42", limit=100)
        recorded_types = {r.event_type for r in records}
        assert recorded_types == {
            "TaskCreated",
            "TaskInputRequired",
            "TaskCompleted",
            "TaskFailed",
            "TaskCancelled",
        }

    def test_records_carry_task_and_tenant_keys(self):
        handler = self._handler()
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        self._emit_full_lifecycle(bus, "task-42", "tenant-a")

        for record in handler.query(task_id="task-42", limit=100):
            assert record.task_id == "task-42"
            assert record.tenant_id == "tenant-a"
            # Full event payload including correlation_id is retained.
            assert record.data["correlation_id"] == "corr"

    def test_full_trail_reconstructable_in_order(self):
        handler = self._handler()
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        self._emit_full_lifecycle(bus, "task-42", "tenant-a")

        records = handler.query(task_id="task-42", limit=100)
        # query() returns most-recent-first; sort to chronological order.
        trail = sorted(records, key=lambda r: r.occurred_at)
        assert [r.event_type for r in trail] == [
            "TaskCreated",
            "TaskInputRequired",
            "TaskCompleted",
            "TaskFailed",
            "TaskCancelled",
        ]

    def test_trail_isolated_per_task_id(self):
        handler = self._handler()
        bus = EventBus()
        bus.subscribe_to_all(handler.handle)

        self._emit_full_lifecycle(bus, "task-1", "tenant-a")
        self._emit_full_lifecycle(bus, "task-2", "tenant-b")

        trail_1 = handler.query(task_id="task-1", limit=100)
        trail_2 = handler.query(task_id="task-2", limit=100)

        assert len(trail_1) == 5
        assert len(trail_2) == 5
        assert all(r.task_id == "task-1" for r in trail_1)
        assert all(r.task_id == "task-2" for r in trail_2)
