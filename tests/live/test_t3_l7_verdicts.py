"""Tier 3 live verification: the L7 verdict the aggregate applied reaches the exported span (#1295).

BLACK-BOX, over the wire twice. A real ``mcp-hangar serve --http`` in front-door
mode exports through its own OTLP gRPC exporter to the in-process receiver in
``_otlp_receiver``. The L7 egress policy is set the way the operator sets it,
with ``PUT /api/mcp_servers/{id}/l7_policy`` under a ``policy:write`` key. Each
call is a stateless modern ``tools/call`` over streamable-HTTP carrying a real
``X-API-Key`` bound to a tenant, and ``lookup`` mirrors its ``region`` argument
in ``Mcp-Param-Region``, which the SDK validates against the body.

Proven, at the receiver, on ``batch.call.<tool>``:

- a tool deny rule reads ``hangar.l7.verdict=deny``, ``rule_kind=tool``, with
  the mode, the policy's content hash and ``hangar.call.outcome=deny``, no
  ``hangar.refusal.*`` and UNSET status;
- an allowed call reads ``allow``/``tool``;
- a validated ``Mcp-Param-Region`` matching a header deny rule reads
  ``deny``/``header``, and the header's value is on no exported span.

The policy is set in this test and asserted in force before any call. When it
is not, this FAILS rather than skips: a verdict that never arrives is the defect
this tier exists to catch. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_l7_verdicts.py -m "live and t3" -o addopts=""
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

from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.observability.conventions import L7, Gate
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TENANT = "tenant-l7"
_ADMIN = "policy-admin"
_SERVER = "region"
_REGION_SERVER = Path(__file__).with_name("_region_server.py")
_ARRIVAL_TIMEOUT_S = 30.0
_MODERN = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "l7-live-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

_POLICY = {
    "tools": {"allow": ["lookup"], "deny": ["ping"]},
    "headers": {"deny": [{"name": "Mcp-Param-Region", "values": ["eu-*"]}]},
    "defaultAction": "Deny",
    "mode": "Enforce",
}

_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: front_door
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
    - principal: "svc:{tenant}"
      role: developer
      scope: global
    - principal: "svc:{admin}"
      role: admin
      scope: global
mcp_servers:
  {server}:
    mode: subprocess
    command: ["{python}", "{stub}"]
    idle_ttl_s: 60
"""


@dataclass
class _Harness:
    receiver: OtlpReceiver
    keys: dict[str, str]
    base_url: str
    run_id: str
    policy_id: str


def _set_policy(base_url: str, admin_key: str) -> str:
    """PUT the policy and read it back; the content hash it is in force under."""
    url = f"{base_url}/api/mcp_servers/{_SERVER}/l7_policy"
    put = httpx.put(url, json=_POLICY, headers={"X-API-Key": admin_key}, timeout=10)
    assert put.status_code == 200, f"the policy was not set: {put.status_code} {put.text[:300]}"
    got = httpx.get(url, headers={"X-API-Key": admin_key}, timeout=10)
    assert got.status_code == 200, f"the policy is not in force: {got.status_code} {got.text[:300]}"
    held, sent = L7Policy.from_dict(got.json()).policy_id, L7Policy.from_dict(_POLICY).policy_id
    assert held == sent, f"the gateway holds another policy: {got.text[:300]}"
    return sent


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    workdir = tmp_path_factory.mktemp("l7_verdicts")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_TENANT, _ADMIN])
    config = _CONFIG.format(
        auth_db=str(auth_db), tenant=_TENANT, admin=_ADMIN, server=_SERVER, python=sys.executable, stub=_REGION_SERVER
    )

    receiver = OtlpReceiver()
    try:
        run_id = uuid.uuid4().hex
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
        env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
        with running_hangar(workdir, config, env) as hangar:
            policy_id = _set_policy(hangar.base_url, keys[_ADMIN])
            yield _Harness(receiver, keys, hangar.base_url, run_id, policy_id)
    finally:
        receiver.stop()


def _post(harness: _Harness, method: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": method,
        "X-API-Key": harness.keys[_TENANT],
        **(headers or {}),
    }
    if "name" in params:
        request_headers["Mcp-Name"] = params["name"]
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": _ENVELOPE}}
    response = httpx.post(f"{harness.base_url}/mcp", headers=request_headers, content=json.dumps(body), timeout=60)
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def _flat_name(harness: _Harness, tool: str) -> str:
    listed = _post(harness, "tools/list", {})["result"]["tools"]
    names = [t["name"] for t in listed if t["name"] == tool or t["name"].endswith(f"_{tool}")]
    assert len(names) == 1, [t["name"] for t in listed]
    return names[0]


def _call(harness: _Harness, tool: str, arguments: dict[str, Any], headers: dict[str, str] | None = None) -> Any:
    return _post(harness, "tools/call", {"name": _flat_name(harness, tool), "arguments": arguments}, headers)


def _span_with(harness: _Harness, tool: str, predicate: Any) -> Received:
    def _probe() -> Received | None:
        return next(
            (
                s
                for s in harness.receiver.spans(harness.run_id)
                if s.name == f"batch.call.{tool}" and predicate(s.attributes)
            ),
            None,
        )

    span = poll(_probe, _ARRIVAL_TIMEOUT_S)
    seen = [dict(s.attributes) for s in harness.receiver.spans(harness.run_id) if s.name == f"batch.call.{tool}"]
    assert span is not None, f"no batch.call.{tool} span with that verdict reached the receiver: {seen}"
    return span


def _l7(span: Received) -> dict[str, Any]:
    return {k: v for k, v in span.attributes.items() if k.startswith("hangar.l7.")}


def test_a_tool_deny_is_exported_with_its_mode_rule_and_policy(harness: _Harness) -> None:
    _call(harness, "ping", {})

    span = _span_with(harness, "ping", lambda a: L7.VERDICT in a)
    assert _l7(span) == {
        L7.VERDICT: "deny",
        L7.MODE: "enforce",
        L7.RULE_KIND: "tool",
        L7.POLICY_ID: harness.policy_id,
    }
    assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY
    assert Gate.REFUSAL_GATE not in span.attributes
    assert span.status_code != 2, "an L7 deny is not an error trace"


def test_an_allowed_call_is_exported_as_allow(harness: _Harness) -> None:
    answer = _call(harness, "lookup", {"region": "us-east-1"}, {"Mcp-Param-Region": "us-east-1"})
    assert not answer["result"].get("isError"), answer

    span = _span_with(harness, "lookup", lambda a: a.get(L7.VERDICT) == "allow")
    assert (span.attributes[L7.RULE_KIND], span.attributes[Gate.CALL_OUTCOME]) == ("tool", Gate.ALLOW)


def test_a_validated_header_deny_is_exported_as_a_header_verdict(harness: _Harness) -> None:
    _call(harness, "lookup", {"region": "eu-west-1"}, {"Mcp-Param-Region": "eu-west-1"})

    span = _span_with(harness, "lookup", lambda a: a.get(L7.VERDICT) == "deny")
    assert (span.attributes[L7.RULE_KIND], span.attributes[L7.MODE]) == ("header", "enforce")
    assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY
    carried = [str(v) for s in harness.receiver.spans(harness.run_id) for v in s.attributes.values()]
    assert not [v for v in carried if "eu-west-1" in v], "the header's value reached a span"
