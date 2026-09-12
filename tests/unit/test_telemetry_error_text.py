"""What a tool returns reaches no span, and no log at INFO or above (GHSA-qwq2-7g49-jxc6).

A backend tool's error text, from an ``isError`` result's content or an upstream
JSON-RPC ``error.message``, travels as the message of ``ToolInvocationError``.
It used to reach a trace backend three ways:

- as the ``batch.call.<tool>`` status description;
- as the ``exception`` event the SDK records, by default, on every Hangar span
  the exception escapes;
- as the ``"<type>: <message>"`` status description the SDK sets on those spans.

These tests drive Hangar's own tracer (``get_tracer``, unpatched) into a real
SDK in-memory exporter. Each one looks for a canary in everything a span
exports: its attributes, its status description and its events. A failure
still gets an ``exception`` event, and its only attribute is ``exception.type``.
``tests/integration/test_trace_propagation_e2e.py`` does the same through the
served app, and ``tests/live/test_t3_export.py`` over a real OTLP receiver.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
import logging
from pathlib import Path
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import mcp_hangar
from mcp_hangar.domain.events import ToolInvocationFailed
from mcp_hangar.domain.exceptions import ToolInvocationError

#: Stands in for anything a tool said: a token, personal data, an echoed argument.
CANARY = "canary-tool-text-5b9e"


@pytest.fixture()
def exported() -> Iterator[Any]:
    """Hangar's real ``get_tracer`` over a local SDK provider, as with a host-registered provider."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with (
        patch("mcp_hangar.observability.tracing._tracing_active", return_value=True),
        patch("opentelemetry.trace.get_tracer_provider", return_value=provider),
    ):
        yield exporter


def _exported_text(span: Any) -> str:
    """Everything a span exports that could carry text, as one string."""
    parts = [span.name, span.status.description or "", *map(str, span.attributes.values())]
    for event in span.events:
        parts += [event.name, *map(str, event.attributes.values())]
    return "\n".join(parts)


def _leaks(exporter: Any) -> list[str]:
    return [s.name for s in exporter.get_finished_spans() if CANARY in _exported_text(s)]


def _outcome(span: Any) -> tuple[str, str | None, Any, list[dict[str, Any]]]:
    """(status, description, error.type, each exception event's attributes)."""
    return (
        span.status.status_code.name,
        span.status.description or None,
        span.attributes.get("error.type"),
        [dict(e.attributes) for e in span.events if e.name == "exception"],
    )


def _type_only(exception_type: str) -> list[dict[str, Any]]:
    """The one ``exception`` event a failure leaves: its type, no message, no stacktrace."""
    return [{"exception.type": exception_type}]


@pytest.mark.otel_sdk
class TestHangarSpans:
    def test_an_exception_escaping_nested_spans_leaves_no_text(self, exported: Any) -> None:
        from mcp_hangar.observability.tracing import get_tracer

        tracer = get_tracer("test")
        with pytest.raises(ToolInvocationError):
            with tracer.start_as_current_span("handler.InvokeToolCommand"):
                with tracer.start_as_current_span("mcp_server.invoke_tool"):
                    raise ToolInvocationError("math", f"tool_error: {CANARY}")

        assert _leaks(exported) == []
        for span in exported.get_finished_spans():
            expected = ("ERROR", None, "ToolInvocationError", _type_only("ToolInvocationError"))
            assert _outcome(span) == expected, span.name
            assert [e.name for e in span.events] == ["exception"], span.name

    def test_the_failure_closest_to_the_span_names_it(self, exported: Any) -> None:
        from mcp_hangar.observability.tracing import get_tracer, upstream_call_span

        with pytest.raises(TimeoutError):
            with get_tracer("test").start_as_current_span("outer"):
                with upstream_call_span("tools/call", {"name": "divide"}) as span:
                    span.set_attribute("error.type", "tool_error")
                    raise TimeoutError(CANARY)

        outcomes = {s.name: _outcome(s) for s in exported.get_finished_spans()}
        assert outcomes == {
            "execute_tool divide": ("ERROR", None, "TimeoutError", _type_only("TimeoutError")),
            "outer": ("ERROR", None, "TimeoutError", _type_only("TimeoutError")),
        }
        assert _leaks(exported) == []

    def test_an_exception_type_not_shaped_like_a_class_name_is_recorded_as_other(self, exported: Any) -> None:
        from mcp_hangar.observability.tracing import get_tracer

        odd = type(f"odd {CANARY}", (RuntimeError,), {})
        with pytest.raises(RuntimeError):
            with get_tracer("test").start_as_current_span("op"):
                raise odd("x")

        [finished] = exported.get_finished_spans()
        assert _outcome(finished) == ("ERROR", None, "_OTHER", _type_only("_OTHER"))
        assert _leaks(exported) == []

    def test_start_span_records_no_exception_text(self, exported: Any) -> None:
        from mcp_hangar.observability.tracing import get_tracer

        with pytest.raises(ValueError):
            with get_tracer("test").start_span("manual"):
                raise ValueError(CANARY)

        assert _leaks(exported) == []

    def test_a_failed_batch_call_span_carries_the_type_not_the_message(self, exported: Any) -> None:
        from mcp_hangar.server.tools.batch.executor import BatchExecutor
        from mcp_hangar.server.tools.batch.models import CallResult

        call = MagicMock(tool="divide", mcp_server="math", call_id="c1", metadata=None)
        failed = CallResult(
            index=0,
            call_id="c1",
            success=False,
            error=f"tool_error: {CANARY}",
            error_type="ToolInvocationError",
            elapsed_ms=1.0,
        )
        executor = BatchExecutor()
        with (
            patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=None),
            patch.object(executor, "_execute_call_inner", return_value=failed),
        ):
            got = executor._execute_call(call, threading.Event(), 60.0, time.perf_counter())

        assert got is failed, "the caller still gets the tool's error text"
        [span] = [s for s in exported.get_finished_spans() if s.name == "batch.call.divide"]
        assert _outcome(span) == ("ERROR", None, "ToolInvocationError", [])
        assert _leaks(exported) == []

    def test_a_handled_failure_records_a_type_only_exception_event(self, exported: Any) -> None:
        from mcp_hangar.observability.tracing import get_tracer, record_handled_failure

        with get_tracer("test").start_as_current_span("mcp_server.cold_start") as span:
            record_handled_failure(span, RuntimeError(CANARY))

        [finished] = exported.get_finished_spans()
        assert _outcome(finished) == ("ERROR", None, "RuntimeError", _type_only("RuntimeError"))
        assert _leaks(exported) == []

    @pytest.mark.parametrize(
        ("given", "recorded"),
        [
            ("ToolInvocationError", "ToolInvocationError"),
            ("-32602", "-32602"),
            ("_Backend.Unavailable", "_Backend.Unavailable"),
            (f"tool_error: {CANARY}", "_OTHER"),
            ("x" * 129, "_OTHER"),
        ],
    )
    def test_mark_span_error_records_only_a_bounded_type(self, exported: Any, given: str, recorded: str) -> None:
        from mcp_hangar.observability.tracing import get_tracer, mark_span_error

        with get_tracer("test").start_as_current_span("op") as span:
            mark_span_error(span, given)

        [finished] = exported.get_finished_spans()
        assert _outcome(finished) == ("ERROR", None, recorded, [])


# Span methods that can carry text: only tracing.py calls them, so the rule
# above holds wherever a span is made.
_TEXT_METHODS = {"record_exception", "set_status", "add_event"}


def test_only_the_tracing_module_writes_span_status_or_events() -> None:
    root = Path(mcp_hangar.__file__).parent
    tracing = root / "observability" / "tracing.py"
    offenders = []
    for source in sorted(root.rglob("*.py")):
        if source == tracing:
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            func = node.func
            by_hand = func.attr in _TEXT_METHODS
            # opentelemetry.trace.get_tracer hands out a tracer without Hangar's defaults.
            foreign_tracer = func.attr == "get_tracer" and isinstance(func.value, ast.Name) and func.value.id == "trace"
            if by_hand or foreign_tracer:
                offenders.append(f"{source.relative_to(root)}:{node.lineno} {func.attr}")
    assert offenders == [], offenders


def test_the_domain_imports_no_tracing_or_opentelemetry_module() -> None:
    """ADR-027: ``domain/`` imports no OpenTelemetry or tracing module, not even for the allowlist.

    Checked on the source rather than on ``sys.modules``: importing any
    ``mcp_hangar`` submodule runs the package root, which already loads the
    tracing module, so a fresh interpreter cannot tell a domain import from
    that. Relative imports are resolved; imports inside functions count too.
    """
    root = Path(mcp_hangar.__file__).parent
    offenders = []
    for source in sorted((root / "domain").rglob("*.py")):
        package = source.relative_to(root.parent).with_suffix("").parts[:-1]
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = list(package[: len(package) - node.level + 1]) if node.level else []
                names = [".".join([*base, node.module] if node.module else base)]
            else:
                continue
            offenders += [
                f"{source.relative_to(root)}:{node.lineno} {name}"
                for name in names
                if name.split(".")[0] == "opentelemetry" or name.startswith("mcp_hangar.observability")
            ]
    assert offenders == [], offenders


def test_the_tracing_module_never_calls_the_sdks_record_exception() -> None:
    """``record_exception`` writes the message and stacktrace; tracing.py adds a type-only event instead."""
    tracing = Path(mcp_hangar.__file__).parent / "observability" / "tracing.py"
    calls = [
        node.lineno
        for node in ast.walk(ast.parse(tracing.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "record_exception"
    ]
    assert calls == [], calls


class TestLogsAtInfoAndAbove:
    def test_the_security_log_carries_the_error_type_not_the_message(self, caplog: pytest.LogCaptureFixture) -> None:
        from mcp_hangar.application.event_handlers.security_handler import LogSecuritySink, SecurityEventHandler

        event = ToolInvocationFailed(
            mcp_server_id="math",
            tool_name="divide",
            error_message=f"tool_error: {CANARY}",
            error_type="tool_error",
        )
        with caplog.at_level(logging.INFO, logger="security"):
            SecurityEventHandler(sink=LogSecuritySink()).handle(event)

        security = [r.getMessage() for r in caplog.records if r.name == "security"]
        assert security, "the failure is still logged"
        assert all(CANARY not in line for line in security), security
        assert any('"error_type": "tool_error"' in line for line in security), security
        assert event.error_message == f"tool_error: {CANARY}", "the event itself keeps the message"

    def test_the_repeated_health_check_failure_log_carries_no_error_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        from mcp_hangar.application.event_handlers.security_handler import LogSecuritySink, SecurityEventHandler
        from mcp_hangar.domain.events import HealthCheckFailed

        handler = SecurityEventHandler(sink=LogSecuritySink())
        event = HealthCheckFailed(
            mcp_server_id="math",
            consecutive_failures=handler.FAILURE_THRESHOLD,
            error_message=f"upstream said: {CANARY}",
        )
        with caplog.at_level(logging.INFO, logger="security"):
            handler.handle(event)

        security = [r.getMessage() for r in caplog.records if r.name == "security"]
        assert any('"consecutive_failures"' in line for line in security), "the failures are still logged"
        assert all(CANARY not in line for line in security), security
        assert event.error_message == f"upstream said: {CANARY}", "the event itself keeps the message"

    @pytest.mark.parametrize(
        ("reply", "logged"),
        [
            ({"error": {"code": -32000, "message": f"upstream said: {CANARY}"}}, "error_type=-32000"),
            ({"error": {"code": f"upstream said: {CANARY}", "message": CANARY}}, "error_type=_OTHER"),
            (OSError(f"upstream said: {CANARY}"), "error_type=OSError"),
        ],
        ids=["json-rpc-code", "json-rpc-code-not-an-integer", "transport-exception"],
    )
    def test_the_health_check_failure_warning_carries_the_error_type_not_the_upstream_text(
        self, reply: Any, logged: str
    ) -> None:
        from mcp_hangar.domain.events import HealthCheckFailed
        from mcp_hangar.domain.model.mcp_server import McpServer
        from mcp_hangar.domain.value_objects import McpServerState

        server = McpServer(mcp_server_id="math", mode="subprocess", command=["echo"])
        server._state = McpServerState.READY
        if isinstance(reply, Exception):
            call = MagicMock(side_effect=reply)
        else:
            call = MagicMock(return_value={"jsonrpc": "2.0", "id": "1", **reply})
        server._client = MagicMock(call=call)

        with patch("mcp_hangar.domain.model.mcp_server.logger") as log:
            assert server.health_check() is False

        warnings = [str(c.args[0]) for c in log.warning.call_args_list]
        assert f"health_check_failed: math, {logged}" in warnings, warnings
        assert all(CANARY not in repr(c) for c in log.method_calls), log.method_calls
        [failed] = [e for e in server.collect_events() if isinstance(e, HealthCheckFailed)]
        assert CANARY in failed.error_message, "the event itself keeps the message"

    @pytest.mark.parametrize(
        ("error", "error_type"),
        [({"code": -32602, "message": "m"}, "-32602"), ({"code": CANARY}, "_OTHER"), ({"message": "m"}, "_OTHER")],
    )
    def test_an_upstream_error_code_is_the_error_type_only_when_it_is_an_integer(
        self, error: dict[str, Any], error_type: str
    ) -> None:
        from mcp_hangar.domain.model.mcp_server import _rpc_error_type

        assert _rpc_error_type(error) == error_type
