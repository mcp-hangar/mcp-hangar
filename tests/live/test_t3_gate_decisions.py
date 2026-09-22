"""Tier 3 live verification: withdrawal and digest-pin decisions reach the exported span (#1285).

BLACK-BOX, over the wire twice. A real ``mcp-hangar serve --http`` exports
through its own OTLP gRPC exporter to the in-process receiver in
``_otlp_receiver``; every decision asserted here crossed the wire as an OTLP
span event on ``batch.call.<tool>``, from a ``hangar_call`` whose request
carried a real ``X-API-Key`` bound to a tenant.

This tier is the one the acceptance criteria of #1285 name for these two
gates, because both are keyed on the caller's tenant, and the tenant reaches
the executor through identity re-binds a mock context does not exercise.
Per-tenant controls have failed open on exactly that path before: a gate that
never saw the tenant would record ``skip`` or ``allow`` here, not ``deny``.

The tenant has ``power`` withdrawn and ``add`` pinned to a digest it cannot
match, under ``digest_enforcement: block``. ``subtract`` has neither.

Proven: the withdrawal gate records ``deny``/``tool_withdrawn`` and names
itself as the refusing gate; the pin records ``deny``/``digest_mismatch`` with
the pinned digest as its revision -- on a cold server as ``deferred`` and then
the re-check's ``deny`` on the same span; an unpinned tool records ``skip``.
Not proven: any other gate, which the unit tests cover. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_gate_decisions.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_hangar.observability.conventions import Gate
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TENANT = "tenant-gates"
_STALE = "d" * 64
_ARRIVAL_TIMEOUT_S = 30.0

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
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
    tool_projection:
      digest_enforcement: block
      tenant_overrides:
        "{tenant}":
          withdrawn: [power]
          pins:
            add: "{stale}"
"""


@dataclass
class _Harness:
    receiver: OtlpReceiver
    api_key: str
    base_url: str
    run_id: str


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("gate_decisions")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    config = _CONFIG.format(
        auth_db=str(auth_db), tenant=_TENANT, python=sys.executable, server=str(_MATH_SERVER), stale=_STALE
    )

    receiver = OtlpReceiver()
    try:
        run_id = uuid.uuid4().hex
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
        env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
        with running_hangar(workdir, config, env) as hangar:
            yield _Harness(receiver=receiver, api_key=keys[_TENANT], base_url=hangar.base_url, run_id=run_id)
    finally:
        receiver.stop()


def _hangar_call(harness: _Harness, tool: str, arguments: dict[str, Any]) -> Any:
    """One ``hangar_call`` over streamable-HTTP as the tenant's key."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> Any:
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.api_key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                calls = [{"mcp_server": "math", "tool": tool, "arguments": arguments}]
                return await session.call_tool("hangar_call", {"calls": calls})

    return asyncio.run(_run())


def _spans(harness: _Harness, tool: str, count: int) -> list[Received]:
    """The ``batch.call.<tool>`` spans from this gateway, once *count* of them have arrived."""

    def _probe() -> list[Received] | None:
        found = [s for s in harness.receiver.spans(harness.run_id) if s.name == f"batch.call.{tool}"]
        return found if len(found) >= count else None

    spans = poll(_probe, _ARRIVAL_TIMEOUT_S)
    assert spans is not None, f"fewer than {count} batch.call.{tool} spans reached the receiver"
    return spans


def _decisions(span: Received) -> list[tuple[Any, Any, Any, Any]]:
    return [
        (a.get(Gate.NAME), a.get(Gate.OUTCOME), a.get(Gate.REASON), a.get(Gate.REVISION))
        for name, a in span.events
        if name == Gate.DECISION_EVENT
    ]


def test_gate_decisions_are_exported_for_the_authenticated_tenant(harness: _Harness) -> None:
    # `add` first: on a server nobody has called yet, its pin is deferred.
    _hangar_call(harness, "add", {"a": 2, "b": 3})
    _hangar_call(harness, "power", {"base": 2, "exponent": 3})
    _hangar_call(harness, "add", {"a": 2, "b": 3})
    _hangar_call(harness, "subtract", {"a": 5, "b": 3})

    (withdrawn,) = _spans(harness, "power", 1)
    assert ("withdrawal", Gate.DENY, "tool_withdrawn", None) in _decisions(withdrawn)
    assert withdrawn.attributes[Gate.REFUSAL_GATE] == "withdrawal"
    assert withdrawn.attributes[Gate.CALL_OUTCOME] == Gate.DENY

    refused = ("digest_pin", Gate.DENY, "digest_mismatch", _STALE)
    deferred = [("digest_pin", Gate.DEFERRED, "catalogue_not_loaded", None)]
    rechecked = [*deferred, ("deferred_digest_pin", Gate.DENY, "digest_mismatch", _STALE)]
    for span in _spans(harness, "add", 2):
        pins = [d for d in _decisions(span) if d[0] in ("digest_pin", "deferred_digest_pin")]
        # Both of a deferred pin's decisions stay on the span; neither overwrites the other.
        assert pins in ([refused], rechecked), pins
        assert span.attributes[Gate.REFUSAL_GATE] == pins[-1][0]
        assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY

    (unpinned,) = _spans(harness, "subtract", 1)
    assert ("digest_pin", Gate.SKIP, "no_pin", None) in _decisions(unpinned)
    assert unpinned.attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
