"""Tier 3 live verification: traces and audit records reach a real OTLP receiver (#1293).

BLACK-BOX: a real ``mcp-hangar serve --http`` process, driven over the shipped
streamable-HTTP ``/mcp`` surface, exports through its own OTLP gRPC exporters to
the in-process receiver in ``_otlp_receiver`` -- a real OTLP endpoint, dialled
over loopback. Nothing in the assertion path is an in-memory exporter: every
record asserted here crossed the wire as OTLP protobuf.

Each gateway gets a unique ``service.instance.id`` through
``OTEL_RESOURCE_ATTRIBUTES``, and every check reads only that run's records, keyed
further on the ``call_id`` the gateway returns (the ``batch.call.id`` span
attribute). Arrival is asynchronous, so every check polls to a deadline.

Proven: a warm call's spans arrive, including the upstream CLIENT span; a call
the tool-access policy denies has a span and no CLIENT span; a
``tool_invocation`` audit record for the warm call arrives under scope
``mcp_hangar.audit``; the spans of a call made right before SIGTERM arrive,
delivered by the shutdown flush alone; and a failing tool's error text reaches
the caller but no exported span -- not a status message, an event or an
attribute (GHSA-qwq2-7g49-jxc6). Not proven: that the audit record is
linked to the call's trace (its trace ID is reported as observed), or anything
about a Collector beyond this receiver. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t3"
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any
import uuid

import pytest

from mcp_hangar.observability.tracing import TRACING_SHUTDOWN_TIMEOUT_S
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_CLIENT = 3  # opentelemetry.proto.trace.v1.Span.SpanKind.SPAN_KIND_CLIENT
_ARRIVAL_TIMEOUT_S = 30.0
# After SIGTERM: uvicorn winds down and the backend is stopped before the flush,
# which is itself bounded by TRACING_SHUTDOWN_TIMEOUT_S.
_SIGTERM_BOUND_S = TRACING_SHUTDOWN_TIMEOUT_S + 15.0
_DENIED_TOOL = "power"
_ARGS = {
    "add": {"a": 2, "b": 3},
    "multiply": {"a": 2, "b": 3},
    "power": {"base": 2, "exponent": 3},
    "divide": {"a": 1, "b": 0},
}
# What the stub backend's `divide` says when it fails; it must reach no span.
_TOOL_ERROR_TEXT = "division by zero"
_STATUS_ERROR = 2  # opentelemetry.proto.trace.v1.Status.StatusCode.STATUS_CODE_ERROR

# One stub backend. `power` is denied by the server's tool-access policy, the
# governance gate the denied call is refused by, before any backend is reached.
_CONFIG = """\
logging:
  level: WARNING
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
    tools:
      deny_list: [{denied}]
"""


def _gateway_env(receiver: OtlpReceiver, run_id: str, **extra: str) -> dict[str, str]:
    """The gateway's environment: the receiver as its OTLP endpoint, ``run_id`` as its instance id."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
    env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
    return env | extra


def _start(workdir: Path, env: dict[str, str]) -> Any:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    config = _CONFIG.format(python=sys.executable, server=_MATH_SERVER, denied=_DENIED_TOOL)
    return running_hangar(workdir, config, env)


def _call(base_url: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable-HTTP and return its JSON payload."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> Any:
        async with open_mcp_streams(f"{base_url}/mcp", {}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    result = asyncio.run(_run())
    # The field's name differs across MCP SDK releases.
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result")
        return inner if isinstance(inner, dict) else structured
    return json.loads(result.content[0].text)


def _hangar_call(base_url: str, tool: str) -> dict[str, Any]:
    """Invoke ``math.<tool>`` through ``hangar_call``; return that call's result."""
    call = {"mcp_server": "math", "tool": tool, "arguments": _ARGS[tool]}
    return _call(base_url, "hangar_call", {"calls": [call]})["results"][0]


def _trace_of(receiver: OtlpReceiver, run_id: str, tool: str, call_id: str) -> list[Received]:
    """The received spans sharing a trace with this call's ``batch.call.<tool>`` span."""
    spans = receiver.spans(run_id)
    for span in spans:
        if span.name == f"batch.call.{tool}" and span.attributes.get("batch.call.id") == call_id:
            return [s for s in spans if s.trace_id == span.trace_id]
    return []


def _upstream_trace(receiver: OtlpReceiver, run_id: str, tool: str, call_id: str) -> list[Received]:
    """``_trace_of``, once it holds the root span and the upstream CLIENT span too."""
    trace = _trace_of(receiver, run_id, tool, call_id)
    names = {s.name for s in trace}
    client = any(s.kind == _CLIENT and s.name == f"execute_tool {tool}" for s in trace)
    return trace if client and "hangar_call" in names else []


def _evidence(label: str, trace: list[Received]) -> str:
    return f"T3 {label}: trace_id={trace[0].trace_id} spans={sorted(s.name for s in trace)}"


@dataclass
class _Run:
    receiver: OtlpReceiver
    run_id: str
    warm: dict[str, Any]
    denied: dict[str, Any]
    failed: dict[str, Any]


@pytest.fixture(scope="module")
def receiver() -> Iterator[OtlpReceiver]:
    """A real OTLP/gRPC receiver on loopback; skips if it cannot start."""
    otlp = OtlpReceiver()
    yield otlp
    otlp.stop()


@pytest.fixture(scope="module")
def exported_run(receiver: OtlpReceiver, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Run]:
    """A gateway exporting to ``receiver``: one warm call, one denied call, one the tool fails."""
    run_id = f"t3-{uuid.uuid4()}"
    # Short batch delays only make arrival prompt; every check still polls.
    env = _gateway_env(receiver, run_id, OTEL_BSP_SCHEDULE_DELAY="200", OTEL_BLRP_SCHEDULE_DELAY="200")
    with _start(tmp_path_factory.mktemp("t3_export"), env) as hangar:
        _call(hangar.base_url, "hangar_start", {"mcp_server": "math"})  # so the next call is warm
        warm = _hangar_call(hangar.base_url, "add")
        denied = _hangar_call(hangar.base_url, _DENIED_TOOL)
        failed = _hangar_call(hangar.base_url, "divide")
        yield _Run(receiver=receiver, run_id=run_id, warm=warm, denied=denied, failed=failed)


def test_warm_call_spans_arrive_under_the_run_instance_id(exported_run: _Run) -> None:
    run = exported_run
    assert run.warm["success"] is True, run.warm

    trace = poll(lambda: _upstream_trace(run.receiver, run.run_id, "add", run.warm["call_id"]), _ARRIVAL_TIMEOUT_S)

    received = sorted({s.name for s in run.receiver.spans(run.run_id)})
    assert trace, f"no complete trace for the warm call under {run.run_id}; received: {received}"
    print(_evidence("warm call", trace))


def test_denied_call_has_a_span_and_no_upstream_client_span(exported_run: _Run) -> None:
    run = exported_run
    assert run.denied["success"] is False, run.denied
    assert run.denied["error_type"] == "ToolAccessDeniedError", run.denied

    trace = poll(lambda: _trace_of(run.receiver, run.run_id, _DENIED_TOOL, run.denied["call_id"]), _ARRIVAL_TIMEOUT_S)

    assert trace, f"no span for the denied call under {run.run_id}"
    assert any(s.name == "policy.check_access" and s.attributes.get("policy.allowed") is False for s in trace), trace
    # A CLIENT span would be a child of the batch.call span already received, so
    # it would have ended, been queued and been exported no later than its parent.
    assert not [s for s in trace if s.kind == _CLIENT], trace
    assert not [s for s in run.receiver.spans(run.run_id) if s.name == f"execute_tool {_DENIED_TOOL}"]
    print(_evidence("denied call", trace))


def test_a_failing_tools_error_text_reaches_the_caller_and_no_exported_span(exported_run: _Run) -> None:
    run = exported_run
    assert run.failed["success"] is False, run.failed
    assert _TOOL_ERROR_TEXT in json.dumps(run.failed), run.failed

    trace = poll(lambda: _upstream_trace(run.receiver, run.run_id, "divide", run.failed["call_id"]), _ARRIVAL_TIMEOUT_S)

    assert trace, f"no complete trace for the failed call under {run.run_id}"
    [call] = [s for s in trace if s.name == "batch.call.divide"]
    assert (call.status_code, call.status_message) == (_STATUS_ERROR, ""), call
    assert call.attributes.get("error.type") == "ToolInvocationError", call
    # Every span this gateway exported, not only this trace's.
    leaked = [s.name for s in run.receiver.spans(run.run_id) if _TOOL_ERROR_TEXT in repr(s)]
    assert leaked == [], leaked
    # The failure is still an exception event, carrying its type and nothing else.
    exceptions = [(s.name, attributes) for s in trace for name, attributes in s.events if name == "exception"]
    assert exceptions, trace
    assert all(set(attributes) == {"exception.type"} for _span, attributes in exceptions), exceptions
    print(_evidence("failed call", trace))
    print(f"T3 failed call exception events: {exceptions}")


def test_audit_record_for_the_warm_call_arrives(exported_run: _Run) -> None:
    run = exported_run

    def _record() -> Received | None:
        for log in run.receiver.logs(run.run_id):
            event, tool = log.attributes.get("mcp.event.name"), log.attributes.get("gen_ai.tool.name")
            if event == "tool_invocation" and tool == "add":
                return log
        return None

    record = poll(_record, _ARRIVAL_TIMEOUT_S)

    assert record is not None, f"no tool_invocation audit record under {run.run_id}"
    assert record.scope == "mcp_hangar.audit", record
    assert record.attributes.get("mcp.server.id") == "math", record
    assert record.attributes.get("mcp.tool.status") == "success", record
    # Reported, not asserted: nothing here promises the record joins the trace.
    warm_trace = _trace_of(run.receiver, run.run_id, "add", run.warm["call_id"])
    same = bool(warm_trace) and warm_trace[0].trace_id == record.trace_id
    print(f"T3 audit: scope={record.scope} trace_id={record.trace_id or '<none>'} matches_warm_call_trace={same}")
    print(f"T3 audit attributes: {record.attributes}")


def test_spans_of_a_call_made_right_before_sigterm_arrive(receiver: OtlpReceiver, tmp_path: Path) -> None:
    run_id = f"t3-{uuid.uuid4()}"
    # The batch timer never fires within the test, so only the shutdown flush
    # can deliver this call's spans.
    env = _gateway_env(receiver, run_id, OTEL_BSP_SCHEDULE_DELAY="600000")
    with _start(tmp_path, env) as hangar:
        _call(hangar.base_url, "hangar_start", {"mcp_server": "math"})
        result = _hangar_call(hangar.base_url, "multiply")
        assert result["success"] is True, result
        assert not _trace_of(receiver, run_id, "multiply", result["call_id"]), "spans arrived before SIGTERM"

        hangar.proc.send_signal(signal.SIGTERM)
        sent = time.monotonic()
        trace = poll(lambda: _upstream_trace(receiver, run_id, "multiply", result["call_id"]), _SIGTERM_BOUND_S)
        arrived_after = time.monotonic() - sent

        assert trace, f"the call's spans did not arrive in {_SIGTERM_BOUND_S}s after SIGTERM:\n{hangar.output()}"
        exit_code = hangar.proc.wait(timeout=_SIGTERM_BOUND_S)
        print(_evidence("SIGTERM call", trace))
        print(f"T3 SIGTERM: arrived {arrived_after:.2f}s after the signal, bound {_SIGTERM_BOUND_S}s, exit={exit_code}")
