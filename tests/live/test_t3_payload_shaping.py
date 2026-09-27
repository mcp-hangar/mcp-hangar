"""Tier 3 live verification: batch truncation reaches the exported trace, and its continuation id does not (#1298).

BLACK-BOX, over the wire twice. A real ``mcp-hangar serve --http`` with
truncation on exports through its own OTLP gRPC exporter to the in-process
receiver in ``_otlp_receiver``, from a ``hangar_call`` whose request carried a
real ``X-API-Key`` bound to a tenant: the continuation is stored for that
caller, which is what the owner binding of the continuation cache needs.

Proven: a ``hangar_call`` the batch budget cuts exports a ``batch.truncate``
span under its ``batch.execute``, carrying the count it cut and that it stored
a continuation, and nothing else; the continuation id the caller holds
appears in no exported span, attribute or event. On a ``front_door`` gateway
with the same budget, a flat ``tools/call`` gets its result whole and opens no
``batch.truncate`` span (#1609). Not proven here: mutation, since no mutator
can be registered through configuration; the unit tests cover it through
``BatchExecutor``. The response size limit is proven live in
``test_t0_response_limit.py`` (#1613).
Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_payload_shaping.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_hangar.observability.conventions import Shaping
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_TENANT = "tenant-shaping"
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
truncation:
  enabled: true
  max_batch_size_bytes: 40
  min_per_response_bytes: 10
  cache_ttl_s: 300
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


_FRONT_DOOR_CONFIG = _CONFIG.replace("mcp_servers:", "tool_access:\n  mode: front_door\nmcp_servers:")


@dataclass
class _Harness:
    receiver: OtlpReceiver
    api_key: str
    base_url: str
    run_id: str


def _serve(tmp_path_factory: pytest.TempPathFactory, template: str) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    workdir = tmp_path_factory.mktemp("payload_shaping")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_TENANT])
    config = template.format(auth_db=str(auth_db), tenant=_TENANT, python=sys.executable, server=str(_MATH_SERVER))

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


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    yield from _serve(tmp_path_factory, _CONFIG)


@pytest.fixture(scope="module")
def front_door(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    yield from _serve(tmp_path_factory, _FRONT_DOOR_CONFIG)


def _call(harness: _Harness, tool: str, arguments: dict[str, Any]) -> Any:
    """One ``tools/call`` over streamable-HTTP as the tenant's key; the result the client got."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> Any:
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.api_key}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    return asyncio.run(_run())


def _hangar_call(harness: _Harness) -> dict[str, Any]:
    """One ``math.add`` through ``hangar_call``; the batch it returns."""
    calls = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]
    result = _call(harness, "hangar_call", {"calls": calls})
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(structured, dict) and "results" in structured:
        return structured
    text = " ".join(getattr(block, "text", "") or "" for block in getattr(result, "content", None) or [])
    return json.loads(text)


def _strings(span: Received) -> list[str]:
    out = [span.name, *map(str, span.attributes.values())]
    for name, attributes in span.events:
        out += [name, *map(str, attributes.values())]
    return out


def test_a_truncated_batch_exports_counts_and_no_continuation_id(harness: _Harness) -> None:
    batch = _hangar_call(harness)
    (call,) = batch["results"]
    assert call.get("truncated") is True and call.get("continuation_id"), batch
    continuation_id = str(call["continuation_id"])

    def _probe() -> tuple[Received, Received, list[Received]] | None:
        # The parent ends after its child, so it may arrive in a later export.
        spans = harness.receiver.spans(harness.run_id)
        for truncate in (s for s in spans if s.name == "batch.truncate"):
            for parent in (s for s in spans if s.span_id == truncate.parent_span_id):
                return truncate, parent, spans
        return None

    arrived = poll(_probe, _ARRIVAL_TIMEOUT_S)
    assert arrived is not None, "no batch.truncate span, with its parent, reached the receiver"
    truncate, parent, spans = arrived

    assert truncate.attributes == {Shaping.TRUNCATED_COUNT: 1, Shaping.CONTINUATION: True}
    assert parent.name == "batch.execute"
    assert [s.name for s in spans if any(continuation_id in v for v in _strings(s))] == []


def test_a_front_door_flat_call_is_served_whole_under_the_same_budget(front_door: _Harness) -> None:
    """The flat call's result is over the 40-byte budget; it is not cut, and nothing is truncated (#1609)."""
    result = _call(front_door, "add", {"a": 1, "b": 2})

    assert not getattr(result, "is_error", False), result
    text = " ".join(getattr(block, "text", "") or "" for block in result.content)
    assert json.loads(text) == {"result": 3.0}, text
    assert len(result.model_dump_json(by_alias=True, exclude_none=True)) > 40, "the result fits the budget"
    # Exported by the time the call's own spans are: wait for them, then look.
    assert poll(
        lambda: [s for s in front_door.receiver.spans(front_door.run_id) if s.name == "batch.execute"] or None,
        _ARRIVAL_TIMEOUT_S,
    )
    assert [s for s in front_door.receiver.spans(front_door.run_id) if s.name == "batch.truncate"] == []
