"""Tier 0 live verification: an upstream response over the read limit fails its call (#1613).

BLACK-BOX. A real ``mcp-hangar serve --http`` whose config sets
``execution.max_response_bytes: 1000000`` and, for its one stdio server, a
smaller ``max_response_bytes: 65536`` of its own. The call is a stateless
modern ``hangar_call`` over streamable-HTTP.

A result of 200 000 bytes is under the process-wide limit and over the
server's, so the server's own limit is the one applied: the call fails with
``error_type: ResponseTooLarge`` and a message naming 65536, where before #1613
it was served (it is under the old 10 MB cap). The next call on the same
server process is served, so the stdio framing is back in step.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_response_limit.py -m "live and t0" -o addopts=""
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

from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_SERVER = "sized"
_SIZED_SERVER = Path(__file__).with_name("_sized_server.py")
_MODERN = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "response-limit-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

_CONFIG = """\
logging:
  level: WARNING
execution:
  max_response_bytes: 1000000
mcp_servers:
  {server}:
    mode: subprocess
    command: ["{python}", "{stub}"]
    idle_ttl_s: 60
    max_response_bytes: 65536
"""


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    workdir = tmp_path_factory.mktemp("response_limit")
    env = {k: v for k, v in os.environ.items() if k != "MCP_MAX_RESPONSE_BYTES"}
    config = _CONFIG.format(server=_SERVER, python=sys.executable, stub=_SIZED_SERVER)
    with running_hangar(workdir, config, env) as hangar:
        yield hangar.base_url


def _call(base_url: str, size: int) -> dict[str, Any]:
    """The one entry of a ``hangar_call`` asking ``sized`` for *size* bytes."""
    params: dict[str, Any] = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": _SERVER, "tool": "sized", "arguments": {"size": size}}]},
        "_meta": _ENVELOPE,
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    response = httpx.post(f"{base_url}/mcp", headers=headers, content=json.dumps(body), timeout=60)
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    answer = json.loads(text)
    assert "result" in answer, answer
    (entry,) = json.loads(answer["result"]["content"][0]["text"])["results"]
    return dict(entry)


def test_a_response_over_the_servers_own_limit_fails_with_response_too_large(base_url: str) -> None:
    entry = _call(base_url, 200_000)

    assert entry["success"] is False, entry
    assert entry["error_type"] == "ResponseTooLarge"
    assert entry["error"] == "The upstream response exceeded the limit of 65536 bytes and was not read."
    assert "truncated_reason" not in entry


def test_the_next_call_on_the_same_server_is_served(base_url: str) -> None:
    _call(base_url, 200_000)

    entry = _call(base_url, 10)

    assert entry["success"] is True, entry
