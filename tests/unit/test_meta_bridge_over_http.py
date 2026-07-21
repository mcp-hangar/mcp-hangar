"""Regression tests: inbound trace context + protocol negotiation over HTTP (#294).

Root cause: over FastMCP's streamable-HTTP transport the batch executor read the
ApplicationContext (``get_context()``, which has no ``request_context``) when
looking for the inbound ``params._meta``. So W3C trace context (SEP-414) and the
stateless protocol/capability negotiation (SEP-2575) silently defaulted for every
HTTP caller -- the same async-context-boundary gap #387 fixed for identity, but
fail-SAFE, not security.

The fix threads the real FastMCP request ``Context`` into
``BatchExecutor.execute()`` (and down into ``_execute_call``) so
``_inbound_meta_dict`` / ``_inbound_trace_meta`` read the ACTUAL inbound
``params._meta``. When ``request_ctx`` is ``None`` (stdio / no request) both
default exactly as before -- empty trace, supported protocol version.

Two layers are pinned:
- executor: the real ``execute()`` path honours ``request_ctx`` (worker threads
  see the client's negotiated version; trace extraction receives the traceparent).
- ``hangar_call``: forwards its FastMCP ``ctx`` as ``request_ctx`` (and ``None``
  on the stdio path).
"""

from __future__ import annotations

import contextvars
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from mcp_hangar._sdk_compat import Context

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.negotiation import get_current_protocol_negotiation
from mcp_hangar.protocol import _META_PROTOCOL_VERSION_KEY, SUPPORTED_PROTOCOL_VERSION
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, hangar_call
from mcp_hangar.server.tools.batch.models import BatchResult

# A version the server does NOT default to, so an assertion that the executor
# observed it proves the client's inbound value was honoured (not the default).
_CLIENT_VERSION = "2099-01-01"
_TRACEPARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
_ONE_CALL = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]


def _fake_http_ctx(meta: object) -> Context:
    """A fake FastMCP Context exposing ``request_context.meta`` (the inbound
    ``params._meta``), mirroring what the streamable-HTTP transport provides and
    what ``_inbound_meta_dict`` / ``_inbound_trace_meta`` read."""
    return cast(Context, SimpleNamespace(request_context=SimpleNamespace(meta=meta)))


# ---------------------------------------------------------------------------
# Executor layer: the REAL execute() path honours request_ctx.
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_context():
    """Minimal ApplicationContext mock so the real executor path runs without
    live infrastructure (mirrors test_context_propagation_batch.py). This is the
    ``get_context()`` ApplicationContext -- deliberately WITHOUT a
    ``request_context``, which is exactly why the bug existed."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = {"ok": True}
    ctx.get_mcp_server.return_value = Mock(
        state=Mock(value="ready"),
        has_tools=False,
        health=Mock(should_degrade=Mock(return_value=False)),
    )
    ctx.mcp_server_exists.return_value = True

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        exec_groups.get.return_value = None
        yield ctx


def _run_with_spy(mock_context: Any, request_ctx: Any) -> tuple[BatchResult, dict[str, Any], list[dict[str, str]]]:
    """Drive one real batch call through ``BatchExecutor.execute`` with the given
    ``request_ctx``, capturing what a worker thread negotiated and every carrier
    handed to ``extract_trace_context``.

    Runs inside a copied context so the request-scoped negotiation contextvar set
    by ``execute()`` does not leak into other tests.
    """
    observed: dict[str, Any] = {}
    carriers: list[dict[str, str]] = []

    def _spy_send(_cmd: Any) -> dict[str, Any]:
        # Worker thread: the negotiation contextvar was set in execute() on the
        # calling thread and inherited here via copy_context().
        neg = get_current_protocol_negotiation()
        observed["version"] = neg.protocol_version if neg is not None else None
        return {"ok": True}

    def _spy_extract(carrier: dict[str, str]) -> Any:
        carriers.append(dict(carrier))
        return None

    mock_context.command_bus.send.side_effect = _spy_send
    executor = BatchExecutor()
    calls = [CallSpec(index=0, call_id="c0", mcp_server="math", tool="add", arguments={"a": 1})]

    def _call() -> BatchResult:
        with patch("mcp_hangar.server.tools.batch.executor.extract_trace_context", _spy_extract):
            return executor.execute(
                batch_id="b1",
                calls=calls,
                max_concurrency=2,
                global_timeout=30.0,
                fail_fast=False,
                request_ctx=request_ctx,
            )

    result = contextvars.copy_context().run(_call)
    return result, observed, carriers


class TestExecutorHonoursRequestCtx:
    def test_inbound_meta_reaches_executor_over_http(self, mock_context):
        """A request_ctx carrying traceparent + protocolVersion in
        ``request_context.meta`` is read by the executor: the worker negotiates
        the client's version and trace extraction receives the traceparent."""
        meta = {"traceparent": _TRACEPARENT, _META_PROTOCOL_VERSION_KEY: _CLIENT_VERSION}
        result, observed, carriers = _run_with_spy(mock_context, _fake_http_ctx(meta))

        assert result.success is True, f"batch failed: {result}"
        # Negotiation: worker saw the CLIENT's version, not the server default.
        assert observed["version"] == _CLIENT_VERSION
        assert observed["version"] != SUPPORTED_PROTOCOL_VERSION
        # Trace: the extraction carrier included the inbound traceparent.
        assert any(c.get("traceparent") == _TRACEPARENT for c in carriers)

    def test_none_request_ctx_defaults_no_crash(self, mock_context):
        """stdio / no-request path: request_ctx=None -> default supported version
        and no traceparent, without crashing (unchanged behavior)."""
        result, observed, carriers = _run_with_spy(mock_context, None)

        assert result.success is True
        assert observed["version"] == SUPPORTED_PROTOCOL_VERSION
        assert all("traceparent" not in c for c in carriers)

    def test_meta_absent_defaults(self, mock_context):
        """A request_ctx whose _meta is None (HTTP request without _meta) still
        defaults cleanly -- no traceparent, supported version."""
        result, observed, carriers = _run_with_spy(mock_context, _fake_http_ctx(None))

        assert result.success is True
        assert observed["version"] == SUPPORTED_PROTOCOL_VERSION
        assert all("traceparent" not in c for c in carriers)


# ---------------------------------------------------------------------------
# hangar_call layer: forwards its FastMCP ctx as request_ctx.
# ---------------------------------------------------------------------------


@pytest.fixture()
def _spy_execute(monkeypatch):
    """Replace the global executor's execute() with a spy capturing the
    ``request_ctx`` it was handed by ``hangar_call``."""
    captured: dict[str, Any] = {}

    def _fake_execute(
        *,
        batch_id: str,
        calls: Any,
        max_concurrency: int,
        global_timeout: float,
        fail_fast: bool,
        request_ctx: Any = None,
    ) -> BatchResult:
        captured["request_ctx"] = request_ctx
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=0.0,
            results=[],
        )

    monkeypatch.setattr(batch._executor, "execute", _fake_execute)
    # Bypass registry-backed validation so the call reaches the executor spy.
    monkeypatch.setattr(batch, "validate_batch", lambda *a, **k: [])
    return captured


class TestHangarCallForwardsRequestCtx:
    def test_http_ctx_forwarded_as_request_ctx(self, _spy_execute):
        """Over an MCP transport, hangar_call threads its FastMCP ctx into the
        executor so the inbound _meta is reachable."""
        ctx = _fake_http_ctx({"traceparent": _TRACEPARENT, _META_PROTOCOL_VERSION_KEY: _CLIENT_VERSION})
        result = hangar_call(calls=list(_ONE_CALL), ctx=ctx)

        assert result["success"] is True
        assert _spy_execute["request_ctx"] is ctx

    def test_stdio_no_ctx_forwards_none(self, _spy_execute):
        """No ctx (stdio / direct) -> request_ctx=None, defaults, no crash."""
        result = hangar_call(calls=list(_ONE_CALL))

        assert result["success"] is True
        assert _spy_execute["request_ctx"] is None
