"""Inbound SEP-414 trace-context extraction from request params._meta (WS-5 #294)."""

from types import SimpleNamespace

from mcp_hangar.server.tools.batch.executor import _inbound_trace_meta


def _ctx(meta: object) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


def test_reads_traceparent_and_tracestate_and_excludes_baggage() -> None:
    from mcp.types import RequestParams

    meta = RequestParams.Meta.model_validate({"traceparent": "00-abc-def-01", "tracestate": "x=1", "baggage": "k=v"})

    out = _inbound_trace_meta(_ctx(meta))

    assert out == {"traceparent": "00-abc-def-01", "tracestate": "x=1"}


def test_none_meta_returns_empty() -> None:
    assert _inbound_trace_meta(_ctx(None)) == {}


def test_missing_request_context_returns_empty() -> None:
    assert _inbound_trace_meta(SimpleNamespace()) == {}


def test_plain_dict_meta_fallback() -> None:
    out = _inbound_trace_meta(_ctx({"traceparent": "00-abc-def-01", "other": "z"}))

    assert out == {"traceparent": "00-abc-def-01"}
