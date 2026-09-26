"""Tier 0 live verification: ``headers.param_validation.required`` covers ``hangar_call`` (#1599).

BLACK-BOX. A real ``mcp-hangar serve --http`` in the default egress topology,
with ``headers.param_validation.required: true`` in its config file. The call
is a stateless modern ``hangar_call`` over streamable-HTTP.

``hangar_call`` declares no ``x-mcp-header``, so an ``Mcp-Param-*`` header it
carries is never checked against the body (ADR-025). Under ``required`` such a
call is refused with ``HEADER_MISMATCH`` (-32020); the same call without the
header is served. Before #1599 ``required`` read only the front door's
listing-failure mark, and the call with the header was served.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_param_validation_required.py \
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

from mcp_hangar.tasks_wire import HEADER_MISMATCH
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_SERVER = "region"
_REGION_SERVER = Path(__file__).with_name("_region_server.py")
_MODERN = "2026-07-28"
_ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": _MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "required-live-probe", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}

_CONFIG = """\
logging:
  level: WARNING
headers:
  param_validation:
    required: true
mcp_servers:
  {server}:
    mode: subprocess
    command: ["{python}", "{stub}"]
    idle_ttl_s: 60
"""


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    workdir = tmp_path_factory.mktemp("param_validation_required")
    config = _CONFIG.format(server=_SERVER, python=sys.executable, stub=_REGION_SERVER)
    with running_hangar(workdir, config, dict(os.environ)) as hangar:
        yield hangar.base_url


def _hangar_call(base_url: str, headers: dict[str, str]) -> Any:
    request_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": _MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "hangar_call",
        **headers,
    }
    params = {
        "name": "hangar_call",
        "arguments": {"calls": [{"mcp_server": _SERVER, "tool": "ping", "arguments": {}}]},
        "_meta": _ENVELOPE,
    }
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    response = httpx.post(f"{base_url}/mcp", headers=request_headers, content=json.dumps(body), timeout=60)
    text = response.text.lstrip()
    if not text.startswith("{"):
        text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def test_a_hangar_call_with_a_param_header_is_refused(base_url: str) -> None:
    answer = _hangar_call(base_url, {"Mcp-Param-Tier": "gold"})

    assert answer.get("error", {}).get("code") == HEADER_MISMATCH, answer
    assert "could not be validated" in answer["error"]["message"]


def test_a_hangar_call_without_one_is_served(base_url: str) -> None:
    answer = _hangar_call(base_url, {})

    assert "result" in answer, answer
    assert not answer["result"].get("isError"), answer
