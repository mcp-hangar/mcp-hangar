"""Tier 3 live verification: an unchecked ``Mcp-Param-*`` header does not decide an L7 verdict (#1597).

BLACK-BOX, over the wire twice. A real ``mcp-hangar serve --http`` in the
default egress topology exports through its own OTLP gRPC exporter to the
in-process receiver in ``_otlp_receiver``. The L7 egress policy is set the way
the operator sets it, with ``PUT /api/mcp_servers/{id}/l7_policy`` under a
``policy:write`` key: its tool rules deny ``lookup`` by default, and a header
rule allows any ``Mcp-Param-Tier``.

The call is a stateless modern ``hangar_call`` over streamable-HTTP carrying a
real ``X-API-Key`` and ``Mcp-Param-Tier``. ``hangar_call`` declares no
``x-mcp-header``, so the SDK compared that header with nothing, and ADR-025
says a selector must not match it. Proven, at the receiver, on
``batch.call.lookup``: ``hangar.l7.verdict=deny`` with ``rule_kind=tool``.
If the header rule decides, the span reads ``allow``/``header`` and this FAILS.

The policy is set in this test and asserted in force before any call. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_l7_header_needs_validation.py \
        -m "live and t3" -o addopts=""
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
    "tools": {"allow": ["ping"]},
    "headers": {"allow": [{"name": "Mcp-Param-Tier", "values": ["*"]}]},
    "defaultAction": "Deny",
    "mode": "Enforce",
}

_CONFIG = """\
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


def _set_policy(base_url: str, admin_key: str) -> None:
    """PUT the policy and read it back, so a call is never made under another one."""
    url = f"{base_url}/api/mcp_servers/{_SERVER}/l7_policy"
    put = httpx.put(url, json=_POLICY, headers={"X-API-Key": admin_key}, timeout=10)
    assert put.status_code == 200, f"the policy was not set: {put.status_code} {put.text[:300]}"
    got = httpx.get(url, headers={"X-API-Key": admin_key}, timeout=10)
    assert got.status_code == 200, f"the policy is not in force: {got.status_code} {got.text[:300]}"
    assert L7Policy.from_dict(got.json()).policy_id == L7Policy.from_dict(_POLICY).policy_id, got.text[:300]


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    workdir = tmp_path_factory.mktemp("l7_header_needs_validation")
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
            _set_policy(hangar.base_url, keys[_ADMIN])
            yield _Harness(receiver, keys, hangar.base_url, run_id)
    finally:
        receiver.stop()


def _hangar_call(harness: _Harness, tool: str, arguments: dict[str, Any], headers: dict[str, str]) -> Any:
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
        "X-API-Key": harness.keys[_TENANT],
        **headers,
    }
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": _SERVER, "tool": tool, "arguments": arguments}]},
        "_meta": _ENVELOPE,
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    response = httpx.post(f"{harness.base_url}/mcp", headers=request_headers, content=json.dumps(body), timeout=60)
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def _verdict_span(harness: _Harness, tool: str) -> Received:
    def _probe() -> Received | None:
        return next(
            (
                s
                for s in harness.receiver.spans(harness.run_id)
                if s.name == f"batch.call.{tool}" and L7.VERDICT in s.attributes
            ),
            None,
        )

    span = poll(_probe, _ARRIVAL_TIMEOUT_S)
    assert span is not None, f"no batch.call.{tool} span with an L7 verdict reached the receiver"
    return span


def test_an_unchecked_header_allow_does_not_outrank_a_tool_default_deny(harness: _Harness) -> None:
    answer = _hangar_call(harness, "lookup", {"region": "eu-west-1"}, {"Mcp-Param-Tier": "gold"})
    assert "result" in answer, answer

    span = _verdict_span(harness, "lookup")
    decided = (span.attributes[L7.VERDICT], span.attributes[L7.RULE_KIND])
    assert decided == ("deny", "tool"), f"the unchecked header decided the call: {decided}"
    assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY
