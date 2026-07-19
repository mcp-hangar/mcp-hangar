"""Tests that logs are correlated to traces via the _add_trace_context processor."""

from __future__ import annotations

import mcp_hangar.observability.tracing as tracing
from mcp_hangar.logging_config import _add_trace_context


def test_no_active_span_is_noop() -> None:
    # Tracing is not initialized in the test process -> helpers return None.
    out = _add_trace_context(None, "info", {"event": "hello"})
    assert "trace_id" not in out
    assert "span_id" not in out


def test_adds_trace_and_span_id_when_in_span(monkeypatch) -> None:
    monkeypatch.setattr(tracing, "get_current_trace_id", lambda: "a" * 32)
    monkeypatch.setattr(tracing, "get_current_span_id", lambda: "b" * 16)
    out = _add_trace_context(None, "info", {"event": "in-span"})
    assert out["trace_id"] == "a" * 32
    assert out["span_id"] == "b" * 16


def test_tracing_error_does_not_break_logging(monkeypatch) -> None:
    def boom() -> str:
        raise RuntimeError("tracing exploded")

    monkeypatch.setattr(tracing, "get_current_trace_id", boom)
    # Must not raise; log record passes through unchanged.
    out = _add_trace_context(None, "info", {"event": "resilient"})
    assert out["event"] == "resilient"
    assert "trace_id" not in out
