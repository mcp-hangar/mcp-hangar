"""Tier 0 live verification: ``header_exposure`` holds on the ``hangar_call`` path (#2253).

BLACK-BOX. A real ``mcp-hangar serve --http`` in the default egress topology,
where ``hangar_call`` is served, called over real streamable HTTP. The region
stub's ``lookup`` annotates its ``region`` argument ``x-mcp-header: Region``;
``ping`` annotates nothing.

- ``region`` declares ``header_exposure: {deny_annotated: [region], on_violation: withdraw}``.
- ``pooled`` is the one member of ``pool``, and the block is the group's.
- ``eager`` declares the same block and starts its member at config load,
  before the projection handler is subscribed. Its start is published once the
  handlers are, so it is projected and governed as a lazily started one (#2257).
- ``joined`` declares the same block and has no member in the file. ``joiner``,
  a server in no group, is added to it over ``POST /api/groups/joined/members``,
  which starts it. Its start is published by the add, so it is projected and
  governed as a lazily started one (#2258).

Before #2253 the block only withheld ``lookup`` from the flat listing, and
``hangar_call`` invoked it. Now the call is refused with
``ToolHeaderWithheldError``, logged as ``batch_call_refused`` with a bounded
reason, and ``ping`` is called as before.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_hangar_call_header_exposure.py \
        -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.live.conftest import RunningHangar, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_REGION_SERVER = Path(__file__).with_name("_region_server.py")
_MODERN = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "header-exposure-live-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

_CONFIG = """\
logging:
  level: INFO
  json_format: true
mcp_servers:
  region:
    mode: subprocess
    command: ["{python}", "{stub}"]
    idle_ttl_s: 60
    header_exposure:
      deny_annotated: ["region"]
      on_violation: withdraw
  pool:
    mode: group
    strategy: round_robin
    auto_start: false
    header_exposure:
      deny_annotated: ["region"]
      on_violation: withdraw
    members:
      - id: pooled
        mode: subprocess
        command: ["{python}", "{stub}"]
        idle_ttl_s: 60
  eager:
    mode: group
    strategy: round_robin
    header_exposure:
      deny_annotated: ["region"]
      on_violation: withdraw
    members:
      - id: eager_member
        mode: subprocess
        command: ["{python}", "{stub}"]
        idle_ttl_s: 60
  joiner:
    mode: subprocess
    command: ["{python}", "{stub}"]
    idle_ttl_s: 60
  joined:
    mode: group
    strategy: round_robin
    header_exposure:
      deny_annotated: ["region"]
      on_violation: withdraw
    members: []
"""


@pytest.fixture(scope="module")
def hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RunningHangar]:
    workdir = tmp_path_factory.mktemp("hangar_call_header_exposure")
    config = _CONFIG.format(python=sys.executable, stub=_REGION_SERVER)
    with running_hangar(workdir, config, dict(os.environ)) as running:
        yield running


def _hangar_call(base_url: str, mcp_server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """One modern ``hangar_call`` over streamable HTTP; the batch's first result."""
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "hangar_call",
            "arguments": {"calls": [{"mcp_server": mcp_server, "tool": tool, "arguments": arguments}]},
            "_meta": _ENVELOPE,
        },
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
    }
    response = httpx.post(f"{base_url}/mcp", headers=headers, content=json.dumps(body), timeout=60)
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    result = json.loads(text)["result"]
    batch = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    first: dict[str, Any] = batch["results"][0]
    return first


def _refusal_lines(hangar: RunningHangar, mcp_server: str) -> list[dict[str, Any]]:
    lines = []
    for raw in hangar.log_path.read_text(errors="replace").splitlines():
        try:
            entry = json.loads(raw[raw.index("{") :]) if "{" in raw else None
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("event") == "batch_call_refused":
            if entry.get("mcp_server") == mcp_server:
                lines.append(entry)
    return lines


@pytest.mark.parametrize("mcp_server", ["region", "pool", "pooled"], ids=["own-block", "group", "group-member"])
def test_a_tool_the_block_withholds_is_refused_and_another_is_served(hangar: RunningHangar, mcp_server: str) -> None:
    if mcp_server == "pool":
        # `pool` does not start its member (`auto_start: false`): a call naming
        # the member starts it, and that start projects its tools.
        assert _hangar_call(hangar.base_url, "pooled", "ping", {})["success"] is True
    refused = _hangar_call(hangar.base_url, mcp_server, "lookup", {"region": "eu"})

    assert refused["success"] is False, refused
    assert refused["error_type"] == "ToolHeaderWithheldError", refused
    [line] = _refusal_lines(hangar, mcp_server)
    assert line["gate"] in {"header_exposure", "deferred_header_exposure"}, line
    assert line["reason"] == "header_exposure_withdraw", line
    assert "error" not in line, "the refusal line keeps no free text"

    served = _hangar_call(hangar.base_url, mcp_server, "ping", {})
    assert served["success"] is True, served


def test_a_member_started_at_config_load_is_governed_like_one_started_by_a_call(hangar: RunningHangar) -> None:
    """Projected at boot, with no call first: ``ping`` is served and ``lookup`` refused by the block.

    Before #2257 nothing projected ``eager_member``, so no verdict could be known
    and both were refused as ``schema_unknown``.
    """
    refused = _hangar_call(hangar.base_url, "eager", "lookup", {"region": "eu"})

    assert refused["success"] is False, refused
    assert refused["error_type"] == "ToolHeaderWithheldError", refused
    [line] = _refusal_lines(hangar, "eager")
    assert line["gate"] in {"header_exposure", "deferred_header_exposure"}, line
    assert line["reason"] == "header_exposure_withdraw", line

    served = _hangar_call(hangar.base_url, "eager", "ping", {})
    assert served["success"] is True, served
    assert len(_refusal_lines(hangar, "eager")) == 1, "ping is not refused"


def test_a_member_added_over_rest_is_governed_like_one_started_by_a_call(hangar: RunningHangar) -> None:
    """Projected by the add, with no call first: ``ping`` is served and ``lookup`` refused by the block.

    Before #2258 the add drained only the group's events, so nothing projected
    ``joiner`` and both were refused as ``schema_unknown``.
    """
    added = httpx.post(f"{hangar.base_url}/api/groups/joined/members", json={"member_id": "joiner"}, timeout=60)
    assert added.status_code == 201, added.text

    refused = _hangar_call(hangar.base_url, "joined", "lookup", {"region": "eu"})

    assert refused["success"] is False, refused
    assert refused["error_type"] == "ToolHeaderWithheldError", refused
    [line] = _refusal_lines(hangar, "joined")
    assert line["gate"] in {"header_exposure", "deferred_header_exposure"}, line
    assert line["reason"] == "header_exposure_withdraw", line

    served = _hangar_call(hangar.base_url, "joined", "ping", {})
    assert served["success"] is True, served
    assert len(_refusal_lines(hangar, "joined")) == 1, "ping is not refused"
