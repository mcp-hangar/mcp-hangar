"""Tier 3 live verification: the epic's tracing scenarios, read at a real OTLP receiver (#1304).

BLACK-BOX. A real ``mcp-hangar serve --http`` is driven over streamable-HTTP
``/mcp`` with a real ``X-API-Key`` bound to a tenant, and exports through its own
OTLP gRPC exporter to the in-process receiver in ``_otlp_receiver``. Every span,
parent, link and event asserted here crossed the wire as OTLP protobuf.

The checks are semantic invariants -- parentage, links, outcomes and bounded
reasons -- never a snapshot of the whole tree, so an added span does not break
them and a wrong parent or outcome does.

Proven: ten concurrent calls to a cold server launch it once, and every other
caller waits in its own span in its own trace, never parented to the start but
linked to it, on either wait path (#1583); a retry to success and to exhaustion
record an attempt event per retried failure, its backoff and the outcome; an
approval granted, denied and expired each report that as ``approval.result``;
gates record allow, skip and deny with bounded reasons, and nothing downstream
of a refusal is spanned; with the collector gone the gateway keeps serving, its
memory stays bounded and the export-failure counter rises. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_trace_scenarios.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_hangar.infrastructure.observability.startup_spans import MECHANISM, ROLE
from mcp_hangar.observability.conventions import Gate, McpServer, Retry
from mcp_hangar.server.tools.batch import executor as batch_executor
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TENANT = "tenant-scenarios"
_APPROVER = "svc:approver"
_FLAKY_SERVER = Path(__file__).with_name("_flaky_server.py")
_ARRIVAL_TIMEOUT_S = 30.0
_BACKOFF_S = 0.2
#: A gate is a stage of the executor's `_GATES`, named without its `_gate_` prefix (ADR-029 s5).
_GATES = frozenset(stage.__name__.removeprefix("_gate_") for stage in batch_executor._GATES)
_BOUNDED = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
#: Spans that only exist once a call is past its gates.
_DOWNSTREAM = ("mcp_server.cold_start", "mcp_server.launch", "invoke_with_retry", "command.send.", "execute_tool ")

_MATH = '    mode: subprocess\n    command: ["{python}", "{math}"]\n    idle_ttl_s: 120\n'
_CONFIG = f"""\
logging:
  level: WARNING
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {{auth_db}}
  role_assignments:
    - principal: "svc:{{tenant}}"
      role: developer
      scope: global
    - principal: "{_APPROVER}"
      role: admin
      scope: global
retry:
  per_mcp_server:
    flaky:
      max_attempts: 3
      backoff: constant
      initial_delay: {_BACKOFF_S}
      jitter: false
      retry_on: [ToolInvocationError]
mcp_servers:
  math:
{_MATH}    tools:
      deny_list: [power]
  cold:
{_MATH}  gated:
{_MATH}    tools:
      approval_list: [add]
      approval_timeout_seconds: 60
  expiring:
{_MATH}    tools:
      approval_list: [add]
      approval_timeout_seconds: 2
  flaky:
    mode: subprocess
    command: ["{{python}}", "{{flaky}}"]
    idle_ttl_s: 120
"""


@dataclass
class _Gateway:
    receiver: OtlpReceiver
    base_url: str
    run_id: str
    key: str
    approver_key: str

    def spans(self) -> list[Received]:
        return self.receiver.spans(self.run_id)


def _env(receiver: OtlpReceiver, run_id: str, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
    env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
    env["OTEL_BSP_SCHEDULE_DELAY"] = "200"  # prompt arrival only; every check still polls
    return env | extra


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    workdir = tmp_path_factory.mktemp("trace_scenarios")
    auth_db = workdir / "auth.db"
    key = gs.seed_tenant_keys(auth_db, [_TENANT])[_TENANT]
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:  # A second principal in the same tenant: approvals are visible only within it.
        approver_key = store.create_key(principal_id=_APPROVER, name="k-approver", tenant_id=_TENANT)
    finally:
        store.close()
    config = _CONFIG.format(
        auth_db=auth_db, tenant=_TENANT, python=sys.executable, math=_MATH_SERVER, flaky=_FLAKY_SERVER
    )
    receiver = OtlpReceiver()
    try:
        run_id = uuid.uuid4().hex
        with running_hangar(workdir, config, _env(receiver, run_id)) as hangar:
            yield _Gateway(receiver, hangar.base_url, run_id, key, approver_key)
    finally:
        receiver.stop()


async def _session_calls(base_url: str, key: str, calls: list[tuple[str, str, dict[str, Any]]]) -> list[dict]:
    """One ``hangar_call`` per entry, each on its own session; the calls start together."""
    from contextlib import AsyncExitStack

    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    headers = {"X-API-Key": key} if key else {}
    async with AsyncExitStack() as stack:
        sessions = []
        for _ in calls:
            read, write = await stack.enter_async_context(open_mcp_streams(f"{base_url}/mcp", headers))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            sessions.append(session)
        results = await asyncio.gather(
            *(
                s.call_tool("hangar_call", {"calls": [{"mcp_server": server, "tool": tool, "arguments": args}]})
                for s, (server, tool, args) in zip(sessions, calls, strict=True)
            )
        )
    return [json.loads(r.content[0].text)["results"][0] for r in results]


def _call(gw: _Gateway, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(_session_calls(gw.base_url, gw.key, [(server, tool, args)]))[0]


def _trace(gw: _Gateway, tool: str, call_id: str) -> tuple[Received, list[Received]]:
    """This call's ``batch.call.<tool>`` span and every span in its trace, once it has arrived."""

    def _probe() -> tuple[Received, list[Received]] | None:
        spans = gw.spans()
        for span in spans:
            if span.name == f"batch.call.{tool}" and span.attributes.get("batch.call.id") == call_id:
                return span, [s for s in spans if s.trace_id == span.trace_id]
        return None

    found = poll(_probe, _ARRIVAL_TIMEOUT_S)
    assert found is not None, f"batch.call.{tool} {call_id} never reached the receiver"
    return found


def _decisions(span: Received) -> list[tuple[Any, Any, Any]]:
    return [
        (a.get(Gate.NAME), a.get(Gate.OUTCOME), a.get(Gate.REASON)) for n, a in span.events if n == Gate.DECISION_EVENT
    ]


def _assert_bounded(decisions: list[tuple[Any, Any, Any]]) -> None:
    outcomes = {Gate.ALLOW, Gate.DENY, Gate.SKIP, Gate.DEFERRED, Gate.ERROR}
    for name, outcome, reason in decisions:
        assert name in _GATES and outcome in outcomes, (name, outcome)
        assert reason is None or _BOUNDED.match(reason), (name, reason)


def _assert_refused_at(call: Received, trace: list[Received], gate: str) -> None:
    """``gate`` refused the call: it is the last decision, and nothing downstream of it was spanned."""
    decisions = _decisions(call)
    _assert_bounded(decisions)
    assert decisions[-1][:2] == (gate, Gate.DENY), decisions
    assert call.attributes[Gate.REFUSAL_GATE] == gate
    assert call.attributes[Gate.CALL_OUTCOME] == Gate.DENY
    assert _BOUNDED.match(call.attributes[Gate.REFUSAL_REASON]), call.attributes
    assert call.status_code == 0, "an expected refusal leaves batch.call UNSET, not ERROR"
    assert not [s.name for s in trace if s.name.startswith(_DOWNSTREAM)], [s.name for s in trace]


@dataclass
class _ColdBurst:
    leader: Received  # the span that performed the start
    launch: Received
    followers: list[list[Received]]  # every other caller's trace
    spans: dict[str, Received]  # every span in the ten traces, by span id


@pytest.fixture(scope="module")
def cold_burst(gateway: _Gateway) -> _ColdBurst:
    """Ten calls, started together on ten sessions, to a server nothing has started."""
    results = asyncio.run(_session_calls(gateway.base_url, gateway.key, [("cold", "add", {"a": 1, "b": 2})] * 10))
    assert all(r["success"] for r in results), results

    traces = [_trace(gateway, "add", r["call_id"])[1] for r in results]
    launches = [
        s for s in gateway.spans() if s.name == "mcp_server.launch" and s.attributes.get(McpServer.ID) == "cold"
    ]
    assert len(launches) == 1, f"{len(launches)} launch spans for one cold server"
    leaders = [s for t in traces for s in t if s.attributes.get(ROLE) == "leader"]
    assert len(leaders) == 1, f"{len(leaders)} spans claim to have performed the start"
    followers = [t for t in traces if leaders[0].trace_id != t[0].trace_id]
    spans = {s.span_id: s for t in traces for s in t}
    return _ColdBurst(leader=leaders[0], launch=launches[0], followers=followers, spans=spans)


def _wait_of(trace: list[Received]) -> Received:
    waits = [s for s in trace if s.name == "mcp_server.startup_wait"]
    assert len(waits) == 1, f"a follower has {len(waits)} wait spans: {sorted(s.name for s in trace)}"
    return waits[0]


def test_ten_concurrent_calls_to_a_cold_server_start_it_once(cold_burst: _ColdBurst) -> None:
    burst = cold_burst
    assert burst.launch.parent_span_id == burst.leader.span_id, "the one launch runs under the one leader"
    assert len(burst.followers) == 9, "the start belongs to exactly one caller's trace"
    for trace in burst.followers:
        wait = _wait_of(trace)
        assert wait.attributes[ROLE] == "waiter" and wait.trace_id != burst.leader.trace_id
        parent = burst.spans.get(wait.parent_span_id)
        assert parent is not None and parent.trace_id == wait.trace_id, "a wait is parented in its own trace"
        for linked_trace, linked_span in wait.links:  # a link, never a parent, to the start
            assert linked_trace == burst.leader.trace_id, wait.links
            assert burst.spans[linked_span].name == "mcp_server.cold_start", burst.spans[linked_span]
    print(f"T3 cold start: mechanisms={sorted(_wait_of(t).attributes[MECHANISM] for t in burst.followers)}")


def test_every_follower_wait_links_to_the_start(cold_burst: _ColdBurst) -> None:
    unlinked = [_wait_of(t).attributes[MECHANISM] for t in cold_burst.followers if not _wait_of(t).links]
    assert unlinked == [], f"{len(unlinked)} of 9 follower waits carry no link: {unlinked}"


def _retry_span(trace: list[Received]) -> Received:
    (span,) = [s for s in trace if s.name == "invoke_with_retry"]
    return span


def _attempts(span: Received) -> list[dict[str, Any]]:
    return [a for n, a in span.events if n == Retry.ATTEMPT_EVENT]


def test_a_retry_to_success_records_each_attempt_and_its_backoff(gateway: _Gateway) -> None:
    result = _call(gateway, "flaky", "flaky", {"key": uuid.uuid4().hex, "failures": 2})
    assert result["success"] is True, result

    call, trace = _trace(gateway, "flaky", result["call_id"])
    retry = _retry_span(trace)
    assert retry.parent_span_id and retry.attributes[Retry.OUTCOME] == Retry.SUCCESS
    attempts = _attempts(retry)
    assert [a[Retry.INDEX] for a in attempts] == [1, 2] and {a[Retry.LAYER] for a in attempts} == {Retry.LAYER_EXECUTOR}
    assert all(a[Retry.BACKOFF_S] == pytest.approx(_BACKOFF_S) for a in attempts), attempts
    assert all(_BOUNDED.match(a[Retry.REASON].lower()) for a in attempts), attempts
    sends = sorted(s.attributes[Retry.INDEX] for s in trace if s.parent_span_id == retry.span_id)
    assert sends == [1, 2, 3], "each attempt is its own command.send span under the retry"
    assert call.attributes[Gate.CALL_OUTCOME] == Gate.ALLOW


def test_a_retry_to_exhaustion_says_so(gateway: _Gateway) -> None:
    result = _call(gateway, "flaky", "flaky", {"key": uuid.uuid4().hex, "failures": 99})
    assert result["success"] is False, result

    call, trace = _trace(gateway, "flaky", result["call_id"])
    retry = _retry_span(trace)
    assert retry.attributes[Retry.OUTCOME] == Retry.EXHAUSTED
    assert [a[Retry.INDEX] for a in _attempts(retry)] == [1, 2], "the last failure is not retried"
    assert call.attributes[Gate.CALL_OUTCOME] == Gate.ERROR


def _held_call(gw: _Gateway, server: str, decision: str | None) -> dict[str, Any]:
    """Call ``server.add``, which needs approval; resolve the hold with ``decision``, or let it expire."""
    box: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: box.update(_call(gw, server, "add", {"a": 2, "b": 3})))
    worker.start()
    if decision is not None:
        headers = {"X-API-Key": gw.approver_key}

        def _pending() -> str | None:
            listed = httpx.get(f"{gw.base_url}/api/approvals", params={"provider_id": server}, headers=headers)
            listed.raise_for_status()
            return next((a["approval_id"] for a in listed.json()), None)

        approval_id = poll(_pending, _ARRIVAL_TIMEOUT_S, interval=0.2)
        assert approval_id, "the held call never produced a pending approval"
        resolved = httpx.post(
            f"{gw.base_url}/api/approvals/{approval_id}/resolve", json={"decision": decision}, headers=headers
        )
        assert resolved.status_code == 200, resolved.text
    worker.join(timeout=_ARRIVAL_TIMEOUT_S * 2)
    assert box, "the held call never returned"
    return box


@pytest.mark.parametrize(
    ("server", "decision", "expected"),
    [("gated", "approve", "granted"), ("gated", "deny", "denied"), ("expiring", None, "expired")],
)
def test_approval_result_is_the_real_outcome(
    gateway: _Gateway, server: str, decision: str | None, expected: str
) -> None:
    result = _held_call(gateway, server, decision)
    assert result["success"] is (expected == "granted"), result

    call, trace = _trace(gateway, "add", result["call_id"])
    flows = [s for s in gateway.spans() if s.name == "approval_gate.flow" and s.attributes.get(McpServer.ID) == server]
    assert [s.attributes.get("approval.result") for s in flows if s.trace_id == call.trace_id] == [expected], flows
    if expected == "granted":
        assert ("approval", Gate.ALLOW, "approved") in _decisions(call)
    else:
        _assert_refused_at(call, trace, "approval")


def test_each_gate_records_a_bounded_decision_and_a_refusal_ends_the_trace(gateway: _Gateway) -> None:
    allowed = _call(gateway, "math", "add", {"a": 2, "b": 3})
    refused = _call(gateway, "math", "power", {"base": 2, "exponent": 3})
    assert allowed["success"] is True and refused["success"] is False, (allowed, refused)

    call, _trace_spans = _trace(gateway, "add", allowed["call_id"])
    decisions = _decisions(call)
    _assert_bounded(decisions)
    assert {d[1] for d in decisions} >= {Gate.ALLOW, Gate.SKIP}, decisions
    assert Gate.DENY not in {d[1] for d in decisions} and call.attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
    assert Gate.REFUSAL_GATE not in call.attributes

    denied, trace = _trace(gateway, "power", refused["call_id"])
    _assert_refused_at(denied, trace, "tool_access")
    print(f"T3 gates: allowed={decisions} refused={_decisions(denied)}")


def _export_failures(base_url: str) -> float:
    text = httpx.get(f"{base_url}/metrics", timeout=5.0).text
    found = re.search(r"^mcp_hangar_otlp_export_failures_total (\S+)$", text, re.MULTILINE)
    return float(found.group(1)) if found else 0.0


def _rss_kib(pid: int) -> int:
    return int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout)


def test_with_the_collector_down_the_gateway_keeps_serving(tmp_path: Path) -> None:
    receiver = OtlpReceiver()
    run_id = uuid.uuid4().hex
    math = _MATH.format(python=sys.executable, math=_MATH_SERVER)
    config = f"logging:\n  level: WARNING\nmcp_servers:\n  math:\n{math}"
    # A small queue and a short export deadline: spans past the queue are dropped, not held.
    small = {"OTEL_BSP_MAX_QUEUE_SIZE": "64", "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "32"}
    env = _env(receiver, run_id, OTEL_EXPORTER_OTLP_TIMEOUT="2", **small)
    try:
        with running_hangar(tmp_path, config, env) as hangar:
            gw = _Gateway(receiver, hangar.base_url, run_id, key="", approver_key="")
            warm = _call(gw, "math", "add", {"a": 1, "b": 1})
            _trace(gw, "add", warm["call_id"])  # the collector was up and receiving
            before = _export_failures(hangar.base_url)
            receiver.stop()

            rss_start = _rss_kib(hangar.proc.pid)
            results = [_call(gw, "math", "add", {"a": i, "b": 1}) for i in range(40)]
            rss_end = _rss_kib(hangar.proc.pid)

            assert all(r["success"] for r in results), [r for r in results if not r["success"]]
            assert poll(lambda: _export_failures(hangar.base_url) > before, _ARRIVAL_TIMEOUT_S), (
                "no export failure counted"
            )
            assert rss_end - rss_start < 64 * 1024, f"RSS grew {rss_end - rss_start} KiB with the collector down"
            after = _export_failures(hangar.base_url)
            print(f"T3 collector down: export failures {before} -> {after}, RSS +{rss_end - rss_start} KiB")
    finally:
        receiver.stop()
