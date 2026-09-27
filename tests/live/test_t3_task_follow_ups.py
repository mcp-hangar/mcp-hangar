"""Tier 3 live verification: a task's follow-ups link to the call that created it (#1615, #1281).

BLACK-BOX. A real ``mcp-hangar serve --http`` with the governed task relay on
and API-key tenants exports through its own OTLP gRPC exporter to the
in-process receiver in ``_otlp_receiver``. Its one upstream, ``_task_server.py``,
answers every ``job`` call with a new task. Each topology runs its own gateway:
on ``front_door`` the task is created by the flat ``tools/call`` of ``job``, on
``egress`` by ``hangar_call``. Both callers declare the tasks extension, as a
client that can poll a task does.

The owner then polls, updates and cancels the task, each in its own request,
and another tenant polls it in between. At the receiver, per ADR-029 s2, s3 and
s8 as #1610 implements them:

- each ``task_relay.<op>`` span is a child of its own request's SDK SERVER
  span, in that request's trace, and carries exactly one link: to the creating
  ``batch.call.job`` span, by trace id and span id;
- the other tenant's poll is ``hangar.task.outcome=not_found`` with no link;
- no task id is in any exported span's name, attributes or events, nor in any
  status but one: the SDK-owned SERVER span of the not-found poll, whose status
  description is ``Task not found: <id>``. That span is outside #1276, and the
  exception is that one span's status, named in the test, not every SERVER span.

A missing link fails the test; nothing here skips on it. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_task_follow_ups.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from mcp_hangar.tasks_wire import EXTENSION_ID
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TASK_SERVER = Path(__file__).with_name("_task_server.py")
_OWNER, _OTHER = "tenant-task-owner", "tenant-task-other"
_SERVER_ID = "jobs"
_TOOL = "job"
_MODERN_VERSION = "2026-07-28"
_ARRIVAL_TIMEOUT_S = 30.0
_SERVER_KIND = 2  # proto SpanKind SERVER
#: Each follow-up the owner makes, its SDK SERVER span's name and the outcome it records.
_FOLLOW_UPS = {
    "task_relay.get": ("tasks/get", "served"),
    "task_relay.update": ("tasks/update", "relayed"),
    "task_relay.cancel": ("tasks/cancel", "confirmed"),
}

_CONFIG = """\
logging:
  level: WARNING
relay_tasks_enabled: true
tool_access:
  mode: {topology}
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "svc:{owner}"
      role: developer
      scope: global
    - principal: "svc:{other}"
      role: developer
      scope: global
mcp_servers:
  {server_id}:
    mode: subprocess
    command: ["{python}", "{task_server}", "{upstream_log}"]
    idle_ttl_s: 120
"""


@dataclass
class _Run:
    """One gateway's scenario: what each request answered, what the upstream saw, and every span."""

    topology: str
    answers: dict[str, dict[str, Any]]
    task_id: str
    upstream: list[dict[str, Any]]
    spans: list[Received]

    def named(self, name: str) -> list[Received]:
        return [s for s in self.spans if s.name == name]

    def by_id(self, span_id: str) -> Received:
        [span] = [s for s in self.spans if s.span_id == span_id]
        return span


def _env(receiver: OtlpReceiver, run_id: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
    env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
    env["OTEL_BSP_SCHEDULE_DELAY"] = "200"  # prompt arrival only; every check still polls
    return env


def _jsonrpc(text: str) -> dict[str, Any]:
    """The JSON-RPC payload, from plain JSON or from an SSE ``data:`` frame."""
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return dict(json.loads(stripped))
    for line in text.splitlines():
        if line.startswith("data: "):
            return dict(json.loads(line[len("data: ") :]))
    raise AssertionError(f"no JSON-RPC payload in: {text[:300]}")


def _post(client: httpx.Client, key: str, method: str, params: dict[str, Any], name: str) -> dict[str, Any]:
    """One request on the modern wire, from a caller that declared the tasks extension."""
    headers = {
        "MCP-Protocol-Version": _MODERN_VERSION,
        "Mcp-Method": method,
        "Mcp-Name": name,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "X-API-Key": key,
    }
    envelope = {
        "io.modelcontextprotocol/protocolVersion": _MODERN_VERSION,
        "io.modelcontextprotocol/clientInfo": {"name": "t3-task-follow-ups", "version": "0"},
        "io.modelcontextprotocol/clientCapabilities": {"extensions": {EXTENSION_ID: {}}},
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": envelope}}
    return _jsonrpc(client.post("/mcp", headers=headers, content=json.dumps(body)).text)


def _created_task_id(topology: str, payload: dict[str, Any]) -> str:
    """The task id the creating call handed back, from a flat call or a ``hangar_call``."""
    result = payload.get("result") or {}
    if topology == "front_door":
        assert result.get("resultType") == "task", payload
        return str(result["taskId"])
    batch = json.loads(result["content"][0]["text"])
    [call] = batch["results"]
    assert call["success"], call
    return str(call["result"]["task"]["taskId"])


def _create(client: httpx.Client, topology: str, key: str) -> dict[str, Any]:
    if topology == "front_door":
        return _post(client, key, "tools/call", {"name": _TOOL, "arguments": {}}, name=_TOOL)
    call = {"calls": [{"mcp_server": _SERVER_ID, "tool": _TOOL, "arguments": {}}]}
    return _post(client, key, "tools/call", {"name": "hangar_call", "arguments": call}, name="hangar_call")


def _listed(client: httpx.Client, key: str) -> list[str]:
    tools = (_post(client, key, "tools/list", {}, name="tools/list").get("result") or {}).get("tools") or []
    return [tool["name"] for tool in tools]


def _all_arrived(receiver: OtlpReceiver, run_id: str) -> list[Received] | None:
    """Every span once the origin and the four follow-ups, each with its SERVER parent, are in."""
    spans = receiver.spans(run_id)
    ids = {s.span_id for s in spans}
    follow_ups = [s for s in spans if s.name.startswith("task_relay.")]
    origin = [s for s in spans if s.name == f"batch.call.{_TOOL}"]
    if origin and len(follow_ups) >= 4 and all(s.parent_span_id in ids for s in follow_ups):
        return spans
    return None


@pytest.fixture(scope="module", params=["front_door", "egress"])
def run(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Run]:
    topology: str = request.param
    workdir = tmp_path_factory.mktemp(f"task_follow_ups_{topology}")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_OWNER, _OTHER])
    owner, other = keys[_OWNER], keys[_OTHER]
    upstream_log = workdir / "upstream.jsonl"
    config = _CONFIG.format(
        topology=topology,
        auth_db=auth_db,
        owner=_OWNER,
        other=_OTHER,
        server_id=_SERVER_ID,
        python=sys.executable,
        task_server=_TASK_SERVER,
        upstream_log=upstream_log,
    )
    receiver = OtlpReceiver()
    try:
        run_id = uuid.uuid4().hex
        with (
            running_hangar(workdir, config, _env(receiver, run_id)) as hangar,
            httpx.Client(base_url=hangar.base_url, timeout=30.0) as client,
        ):
            if topology == "front_door":  # the flat names appear once the boot warm-up lists them
                assert poll(lambda: _TOOL in _listed(client, owner), _ARRIVAL_TIMEOUT_S), hangar.output()
            answers: dict[str, dict[str, Any]] = {"created": _create(client, topology, owner)}
            task_id = _created_task_id(topology, answers["created"])

            def follow(key: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
                return _post(client, key, method, {"taskId": task_id, **params}, name=task_id)

            answers["get"] = follow(owner, "tasks/get", {})
            answers["update"] = follow(owner, "tasks/update", {"inputResponses": {"r1": {"ok": True}}})
            answers["foreign_get"] = follow(other, "tasks/get", {})
            answers["cancel"] = follow(owner, "tasks/cancel", {})

            spans = poll(lambda: _all_arrived(receiver, run_id), _ARRIVAL_TIMEOUT_S)
            assert spans is not None, f"the follow-up spans never reached the receiver:\n{hangar.output()}"
            upstream = [json.loads(line) for line in upstream_log.read_text().splitlines()]
            yield _Run(topology, answers, task_id, upstream, spans)
    finally:
        receiver.stop()


def test_the_task_was_created_and_each_follow_up_was_answered(run: _Run) -> None:
    assert run.task_id.startswith("task-"), run.answers["created"]
    for method in ("get", "update", "cancel"):
        assert "result" in run.answers[method], (method, run.answers[method])
    assert run.answers["foreign_get"]["error"]["code"] == -32602, run.answers["foreign_get"]
    # The owner's update and cancel reached the upstream for this task, once each.
    relayed = [e["method"] for e in run.upstream if e["method"] in ("tasks/update", "tasks/cancel")]
    assert relayed == ["tasks/update", "tasks/cancel"], run.upstream
    assert {e["task_id"] for e in run.upstream if e["method"].startswith("tasks/")} == {run.task_id}, run.upstream


@pytest.mark.parametrize("name", sorted(_FOLLOW_UPS))
def test_each_follow_up_is_a_child_of_its_server_span_linked_to_the_creating_call(run: _Run, name: str) -> None:
    server_name, outcome = _FOLLOW_UPS[name]
    [origin] = run.named(f"batch.call.{_TOOL}")
    [span] = [s for s in run.named(name) if s.attributes.get("hangar.task.outcome") == outcome]
    parent = run.by_id(span.parent_span_id)

    assert (parent.name, parent.kind) == (server_name, _SERVER_KIND), parent
    assert span.trace_id == parent.trace_id, "a follow-up is spanned in its own request's trace"
    assert span.trace_id != origin.trace_id, "a follow-up is never parented on the call that created the task"
    assert span.links == ((origin.trace_id, origin.span_id),), (
        f"{name} must carry exactly one link, to batch.call.{_TOOL} "
        f"{(origin.trace_id, origin.span_id)}; it carries {span.links}"
    )
    assert span.attributes["mcp.server.id"] == _SERVER_ID
    assert span.attributes["gen_ai.tool.name"] == _TOOL


def _not_found(run: _Run) -> Received:
    [span] = [s for s in run.named("task_relay.get") if s.attributes.get("hangar.task.outcome") == "not_found"]
    return span


def test_a_poll_by_another_tenant_is_not_found_with_no_link(run: _Run) -> None:
    span = _not_found(run)
    parent = run.by_id(span.parent_span_id)

    assert span.links == (), "a foreign task yields no link to its origin"
    assert "mcp.server.id" not in span.attributes and "gen_ai.tool.name" not in span.attributes
    assert (parent.name, parent.kind) == ("tasks/get", _SERVER_KIND)
    assert span.trace_id == parent.trace_id


def test_no_task_id_is_in_any_exported_span(run: _Run) -> None:
    task_ids = {run.task_id, *(e["task_id"] for e in run.upstream if e["task_id"])}
    # The one exception, and only its status: the SDK-owned SERVER span of the
    # not-found poll describes its status as `Task not found: <id>`. It is not a
    # Hangar span and is outside #1276 (ADR-029, Neutral).
    exempt = _not_found(run).parent_span_id
    for span in run.spans:
        exported = {"name": span.name, "attributes": span.attributes, "events": span.events}
        if span.span_id != exempt:
            exported["status"] = span.status_message
        text = json.dumps(exported, default=str)
        assert not [tid for tid in task_ids if tid in text], (span.name, exported)
