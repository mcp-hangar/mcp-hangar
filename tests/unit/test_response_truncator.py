"""Unit tests for ResponseTruncator mutator."""

import json

import pytest

from mcp_hangar.application.mutators.response_truncator import ResponseTruncator
from mcp_hangar.domain.contracts.mutator import MutationContext
from mcp_hangar.domain.events import ResponseTruncated


def _ctx(result_value: str = "hello", direction: str = "response") -> MutationContext:
    return MutationContext(
        method="tools/call",
        direction=direction,
        payload={"result": result_value},
        correlation_id="test-corr",
    )


class TestResponseTruncator:
    def test_under_limit_no_truncation(self):
        events: list[object] = []
        t = ResponseTruncator(max_bytes=1000, event_collector=events)
        result = t.mutate(_ctx("short"))
        assert result.changed is False
        assert result.payload["result"] == "short"
        assert len(events) == 0

    def test_at_limit_no_truncation(self):
        events: list[object] = []
        data = "x" * 100
        t = ResponseTruncator(max_bytes=len(json.dumps(data, separators=(",", ":")).encode()), event_collector=events)
        result = t.mutate(_ctx(data))
        assert result.changed is False
        assert len(events) == 0

    def test_over_limit_truncates(self):
        events: list[object] = []
        data = "x" * 500
        t = ResponseTruncator(max_bytes=100, event_collector=events)
        result = t.mutate(_ctx(data))
        assert result.changed is True
        truncated_bytes = len(json.dumps(result.payload["result"], separators=(",", ":")).encode())
        assert truncated_bytes <= 100 + 10  # allow small overhead from re-serialization
        assert len(events) == 1

    def test_over_limit_emits_event(self):
        events: list[object] = []
        data = "x" * 500
        t = ResponseTruncator(max_bytes=100, event_collector=events)
        t.mutate(_ctx(data))
        assert len(events) == 1
        evt = events[0]
        assert isinstance(evt, ResponseTruncated)
        assert evt.method == "tools/call"
        assert evt.correlation_id == "test-corr"
        assert evt.original_size > 100
        assert evt.truncated_size <= 100
        assert evt.max_size == 100

    def test_request_direction_skipped(self):
        t = ResponseTruncator(max_bytes=10)
        result = t.mutate(_ctx("x" * 500, direction="request"))
        assert result.changed is False

    def test_no_result_field_skipped(self):
        t = ResponseTruncator(max_bytes=10)
        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"other": "data"},
            correlation_id="x",
        )
        result = t.mutate(ctx)
        assert result.changed is False

    def test_rejects_non_positive_max_bytes(self):
        with pytest.raises(ValueError, match="max_bytes must be positive"):
            ResponseTruncator(max_bytes=0)

    def test_rejects_negative_max_bytes(self):
        with pytest.raises(ValueError, match="max_bytes must be positive"):
            ResponseTruncator(max_bytes=-1)

    def test_priority_hint(self):
        t = ResponseTruncator()
        assert t.priority_hint == 1000

    def test_applies_to(self):
        t = ResponseTruncator()
        assert t.applies_to == frozenset({"tools/call"})

    def test_protocol_conformance(self):
        from mcp_hangar.domain.contracts.mutator import IMutator

        assert isinstance(ResponseTruncator(), IMutator)

    def test_no_event_collector_no_error(self):
        t = ResponseTruncator(max_bytes=10)
        result = t.mutate(_ctx("x" * 500))
        assert result.changed is True
