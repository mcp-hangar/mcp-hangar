"""Coverage guard for task-relay lifecycle metrics (ADR-014 Phase 3, finding #21).

The relay seam emits Task* lifecycle events + DigestMismatchInTask onto the
provenance chain. The fail-closed paths (TaskFailed, DigestMismatchInTask) are
the ones most in need of alerting, so every such event MUST increment a
Prometheus counter via :class:`MetricsEventHandler`.

Two guards:

1. Behavioral: publish each Task*/DigestMismatchInTask event through a
   ``MetricsEventHandler`` and assert its counter incremented (with the
   tenant_id label, and ``reason`` for TaskFailed).
2. Reflective: enumerate every ``DomainEvent`` subclass in ``domain.events``
   whose name is Task* or DigestMismatchInTask and assert each one is wired to
   a counter here -- so a future Task* event added WITHOUT a metric fails loudly.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.application.event_handlers import MetricsEventHandler
from mcp_hangar.domain import events as domain_events
from mcp_hangar.domain.events import (
    DigestMismatchInTask,
    DomainEvent,
    TaskCancelled,
    TaskCompleted,
    TaskConsentDecided,
    TaskCreated,
    TaskFailed,
    TaskInputRequired,
)

_TENANT = "tenant-metrics-guard"

# Each Task*/DigestMismatchInTask event class -> (built instance, the counter it
# must increment, the labels that counter is incremented with). Adding a new
# Task* event to domain.events without extending this map trips the reflective
# guard below.
EVENT_COUNTER_MAP: dict[type[DomainEvent], tuple[DomainEvent, object, dict[str, str]]] = {
    TaskCreated: (
        TaskCreated(task_id="t1", tenant_id=_TENANT),
        prometheus_metrics.TASK_RELAYED_TOTAL,
        {"tenant_id": _TENANT},
    ),
    TaskCompleted: (
        TaskCompleted(task_id="t2", tenant_id=_TENANT),
        prometheus_metrics.TASK_COMPLETED_TOTAL,
        {"tenant_id": _TENANT},
    ),
    TaskFailed: (
        TaskFailed(task_id="t3", tenant_id=_TENANT, error_type="digest_mismatch"),
        prometheus_metrics.TASK_FAILED_TOTAL,
        {"tenant_id": _TENANT, "reason": "digest_mismatch"},
    ),
    TaskCancelled: (
        TaskCancelled(task_id="t4", tenant_id=_TENANT),
        prometheus_metrics.TASK_CANCELLED_TOTAL,
        {"tenant_id": _TENANT},
    ),
    TaskInputRequired: (
        TaskInputRequired(task_id="t5", tenant_id=_TENANT),
        prometheus_metrics.TASK_INPUT_REQUIRED_TOTAL,
        {"tenant_id": _TENANT},
    ),
    DigestMismatchInTask: (
        DigestMismatchInTask(task_id="t6", tenant_id=_TENANT),
        prometheus_metrics.TASK_DIGEST_DRIFT_TOTAL,
        {"tenant_id": _TENANT},
    ),
    TaskConsentDecided: (
        TaskConsentDecided(task_id="t7", tenant_id=_TENANT, granted=True),
        prometheus_metrics.TASK_CONSENT_DECIDED_TOTAL,
        {"tenant_id": _TENANT, "granted": "true"},
    ),
}


def _counter_value(counter: object, **labels: str) -> float:
    """Read the current value of ``counter`` for an exact label set (0.0 if absent)."""
    for sample in counter.collect():  # type: ignore[attr-defined]
        if all(sample.labels.get(k) == v for k, v in labels.items()):
            return sample.value
    return 0.0


def _discover_task_event_classes() -> set[type[DomainEvent]]:
    """All DomainEvent subclasses in domain.events named Task* or DigestMismatchInTask."""
    found: set[type[DomainEvent]] = set()
    for _, obj in inspect.getmembers(domain_events, inspect.isclass):
        if not issubclass(obj, DomainEvent) or obj is DomainEvent:
            continue
        if obj.__name__.startswith("Task") or obj.__name__ == "DigestMismatchInTask":
            found.add(obj)
    return found


@pytest.mark.parametrize("event_cls", list(EVENT_COUNTER_MAP), ids=lambda c: c.__name__)
def test_task_event_increments_its_counter(event_cls: type[DomainEvent]) -> None:
    """Publishing each Task*/DigestMismatchInTask event increments its counter."""
    event, counter, labels = EVENT_COUNTER_MAP[event_cls]
    before = _counter_value(counter, **labels)

    MetricsEventHandler().handle(event)

    after = _counter_value(counter, **labels)
    assert after == before + 1.0, f"{event_cls.__name__} did not increment {counter.name} for {labels}"  # type: ignore[attr-defined]


def test_every_task_event_class_is_wired_to_a_metric() -> None:
    """Reflective guard: no Task*/DigestMismatchInTask event may lack a counter.

    If a new lifecycle event is added to domain.events, this fails until it is
    given a MetricsEventHandler branch + an EVENT_COUNTER_MAP entry here.
    """
    discovered = _discover_task_event_classes()
    mapped = set(EVENT_COUNTER_MAP)
    missing = discovered - mapped
    assert not missing, f"Task* event(s) without a metric branch: {sorted(c.__name__ for c in missing)}"
    # And nothing stale in the map.
    assert mapped <= discovered, f"EVENT_COUNTER_MAP references non-events: {mapped - discovered}"


def test_task_failed_reason_label_uses_error_type() -> None:
    """The task_failed_total{reason} label is sourced from the event's error_type."""
    event = TaskFailed(task_id="t-reason", tenant_id=_TENANT, error_type="upstream_timeout")
    before = _counter_value(prometheus_metrics.TASK_FAILED_TOTAL, tenant_id=_TENANT, reason="upstream_timeout")

    MetricsEventHandler().handle(event)

    after = _counter_value(prometheus_metrics.TASK_FAILED_TOTAL, tenant_id=_TENANT, reason="upstream_timeout")
    assert after == before + 1.0


def test_missing_tenant_id_normalized_to_unknown() -> None:
    """A None tenant_id is normalized to 'unknown' so the label never carries None."""
    event = TaskCreated(task_id="t-anon", tenant_id=None)
    before = _counter_value(prometheus_metrics.TASK_RELAYED_TOTAL, tenant_id="unknown")

    MetricsEventHandler().handle(event)

    after = _counter_value(prometheus_metrics.TASK_RELAYED_TOTAL, tenant_id="unknown")
    assert after == before + 1.0
