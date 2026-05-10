"""End-to-end integration tests for ADR-004 + ADR-005 domain wiring.

Verifies that tool invocations pass through validators -> mutators -> audit,
and that a digest mismatch produces a DigestMismatchEvent consumed by a handler.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar.application.mutators.response_truncator import ResponseTruncator
from mcp_hangar.application.services.mutator_pipeline import MutatorPipeline
from mcp_hangar.domain.contracts.hook_subscriber import IHookSubscriber
from mcp_hangar.domain.contracts.mutator import MutationContext
from mcp_hangar.domain.events import DigestMismatchEvent, DomainEvent, ResponseTruncated
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.services.digest_validator import DigestValidator
from mcp_hangar.domain.value_objects.event_pattern import EventPattern
from mcp_hangar.domain.value_objects.hook import Hook, HookPhase
from mcp_hangar.domain.value_objects.tool_digest import (
    DigestEnforcement,
    DigestPolicy,
    DigestUnknownPolicy,
    ToolDigest,
)
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.api.ws.filters import matches_filters


SAMPLE_TOOL: dict[str, Any] = {
    "name": "get_weather",
    "description": "Returns current weather for a city.",
    "inputSchema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}


def _make_policy_with_correct_digest() -> DigestPolicy:
    digest = compute_tool_digest(SAMPLE_TOOL)
    return DigestPolicy(
        enforcement=DigestEnforcement.BLOCK,
        unknown=DigestUnknownPolicy.WARN,
        allowlist=frozenset({digest}),
    )


def _make_policy_with_wrong_digest() -> DigestPolicy:
    wrong = ToolDigest(tool_name="get_weather", sha256="a" * 64)
    return DigestPolicy(
        enforcement=DigestEnforcement.BLOCK,
        unknown=DigestUnknownPolicy.WARN,
        allowlist=frozenset({wrong}),
    )


class TestDigestValidationToEventBus:
    """DigestValidator -> DigestMismatchEvent -> EventBus -> subscriber."""

    def test_digest_match_produces_no_event(self):
        validator = DigestValidator(_make_policy_with_correct_digest())
        result = validator.validate_tool(SAMPLE_TOOL, "srv-1", "corr-1")

        assert result.valid is True
        assert result.blocked is False
        assert result.event is None

    def test_digest_mismatch_produces_event_consumed_by_handler(self):
        validator = DigestValidator(_make_policy_with_wrong_digest())
        result = validator.validate_tool(SAMPLE_TOOL, "srv-1", "corr-1")

        assert result.valid is False
        assert result.blocked is True
        assert result.event is not None

        captured: list[DomainEvent] = []
        bus = EventBus()
        bus.subscribe(DigestMismatchEvent, captured.append)
        bus.publish(result.event)

        assert len(captured) == 1
        event = captured[0]
        assert isinstance(event, DigestMismatchEvent)
        assert event.tool_name == "get_weather"
        assert event.expected_digest == "a" * 64
        assert event.enforcement == "block"

    def test_digest_mismatch_reaches_hook_subscriber(self):
        validator = DigestValidator(_make_policy_with_wrong_digest())
        result = validator.validate_tool(SAMPLE_TOOL, "srv-1", "corr-1")

        hook_log: list[Hook] = []

        class _Recorder(IHookSubscriber):
            def on_hook(self, hook: Hook) -> None:
                hook_log.append(hook)

        bus = EventBus()
        bus.subscribe_hooks(_Recorder())
        assert result.event is not None
        bus.publish(result.event)

        assert len(hook_log) == 1
        assert hook_log[0].phase == HookPhase.OBSERVE
        assert isinstance(hook_log[0].event, DigestMismatchEvent)


class TestMutatorPipelineEndToEnd:
    """MutatorPipeline + ResponseTruncator -> ResponseTruncated event."""

    def test_truncator_truncates_and_emits_event(self):
        events: list[Any] = []
        truncator = ResponseTruncator(max_bytes=50, event_collector=events)

        pipeline = MutatorPipeline()
        pipeline.register(truncator)

        big_result = "x" * 200
        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"result": big_result},
            correlation_id="corr-2",
        )
        out = pipeline.execute(ctx)

        assert out.changed is True
        assert len(out.payload["result"]) < len(big_result)

        assert len(events) == 1
        assert isinstance(events[0], ResponseTruncated)
        assert events[0].method == "tools/call"
        assert events[0].original_size > 50

    def test_truncator_skips_small_response(self):
        events: list[Any] = []
        truncator = ResponseTruncator(max_bytes=50000, event_collector=events)

        pipeline = MutatorPipeline()
        pipeline.register(truncator)

        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"result": "small"},
            correlation_id="corr-3",
        )
        out = pipeline.execute(ctx)

        assert out.changed is False
        assert len(events) == 0

    def test_truncator_event_reaches_event_bus(self):
        events: list[Any] = []
        truncator = ResponseTruncator(max_bytes=50, event_collector=events)

        pipeline = MutatorPipeline()
        pipeline.register(truncator)

        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"result": "y" * 200},
            correlation_id="corr-4",
        )
        pipeline.execute(ctx)

        bus_log: list[DomainEvent] = []
        bus = EventBus()
        bus.subscribe(ResponseTruncated, bus_log.append)

        for ev in events:
            bus.publish(ev)

        assert len(bus_log) == 1
        assert isinstance(bus_log[0], ResponseTruncated)


class TestFullPipelineFlow:
    """Digest validation -> mutator pipeline -> event bus audit trail."""

    def test_mismatch_then_truncation_events_all_reach_audit(self):
        audit_log: list[DomainEvent] = []
        bus = EventBus()
        bus.subscribe_to_all(audit_log.append)

        # Step 1: digest validation produces mismatch event
        validator = DigestValidator(_make_policy_with_wrong_digest())
        result = validator.validate_tool(SAMPLE_TOOL, "srv-1", "corr-full")

        assert result.event is not None
        bus.publish(result.event)

        # Step 2: mutator pipeline truncates oversized response
        mutator_events: list[Any] = []
        truncator = ResponseTruncator(max_bytes=30, event_collector=mutator_events)
        pipeline = MutatorPipeline()
        pipeline.register(truncator)

        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"result": "z" * 300},
            correlation_id="corr-full",
        )
        pipeline.execute(ctx)

        for ev in mutator_events:
            bus.publish(ev)

        # Both events reached the audit subscriber
        assert len(audit_log) == 2
        types = {type(e) for e in audit_log}
        assert DigestMismatchEvent in types
        assert ResponseTruncated in types

        # Same correlation_id across the pipeline
        assert all(getattr(e, "correlation_id", None) == "corr-full" for e in audit_log)


class TestWildcardSubscriptionFiltering:
    """EventPattern + matches_filters integration with real events."""

    def test_wildcard_pattern_filters_events(self):
        pattern = EventPattern("tools/*")
        assert pattern.matches("tools/call") is True
        assert pattern.matches("tools/list") is True
        assert pattern.matches("resources/read") is False

    def test_matches_filters_with_wildcard_and_real_event(self):
        mismatch = DigestMismatchEvent(
            mcp_server_id="srv-1",
            tool_name="get_weather",
            expected_digest="a" * 64,
            observed_digest="b" * 64,
            enforcement="block",
            correlation_id="corr-w",
        )
        # DigestMismatchEvent event_type has no "/" -> won't match tools/*
        filters_tools = {"event_types": ["tools/*"]}
        assert matches_filters(mismatch, filters_tools) is False

        # Exact match by class name works
        filters_exact = {"event_types": ["DigestMismatchEvent"]}
        assert matches_filters(mismatch, filters_exact) is True

        # Star matches everything
        filters_star = {"event_types": ["*"]}
        assert matches_filters(mismatch, filters_star) is True
