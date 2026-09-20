"""Tier 0 live verification: a front door's flat call runs the configured interceptors (#1425).

BLACK-BOX against two REAL ``mcp-hangar serve --http`` processes over the
shipped streamable-HTTP ``/mcp`` surface, both configured with
``interceptors.validators: [{type: payload_size, max_bytes: 200}]``. One is a
``front_door``, the other the default ``egress``, because ``hangar_call`` is
not on a front door's surface. The same oversized arguments must be refused on
the front door's flat ``tools/call`` with the error ``hangar_call`` gives on the
egress gateway, and arguments under the cap must be served.

The flat call dispatched through an executor with an empty interceptor pipeline,
so it served the oversized call. Per-request behaviour on this path has had
fail-opens that a unit test with a mock context did not see, which is why this
is driven over the real transport. Skip-safe like the rest of the tier. Run
with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_interceptors.py -m "live and t0" -o addopts=""
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import pytest

from tests.live import _group_support as gs
from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

_TENANT = "tenant-interceptors"
_CAP = 200
#: Arguments whose ``tools/call`` payload is over the cap. Never reaches the upstream.
_OVER = {"a": 1, "b": 2, "pad": "x" * 500}
_UNDER = {"a": 1, "b": 2}

_CONFIG = """\
logging:
  level: WARNING
{topology}auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  role_assignments:
    - principal: "group:{svc_group}"
      role: service-account
      scope: global
interceptors:
  validators:
    - type: payload_size
      max_bytes: {cap}
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


@dataclass
class _Gateway:
    base_url: str
    api_key: str

    def call(self, tool: str, arguments: dict[str, Any]) -> tuple[bool, str]:
        """``(is_error, text)`` of one ``tools/call``, as the client receives it."""
        from mcp import ClientSession

        from tests.live._mcp_client import open_mcp_streams

        async def _run() -> tuple[bool, str]:
            async with open_mcp_streams(f"{self.base_url}/mcp", {"X-API-Key": self.api_key}) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
            is_error = bool(getattr(result, "is_error", getattr(result, "isError", False)))
            return is_error, "".join(getattr(block, "text", "") for block in result.content)

        return asyncio.run(_run())


@pytest.fixture(scope="module")
def gateways(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, _Gateway]]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    with ExitStack() as stack:
        served: dict[str, _Gateway] = {}
        for topology in ("front_door", "egress"):
            workdir = tmp_path_factory.mktemp(f"interceptors_{topology}")
            auth_db = workdir / "auth.db"
            try:
                keys = gs.seed_tenant_keys(auth_db, [_TENANT])
            except Exception as exc:  # noqa: BLE001 -- fixture prerequisite: skip, never fail
                pytest.skip(f"could not seed tenant API keys: {exc}")
            config = _CONFIG.format(
                topology="tool_access:\n  mode: front_door\n" if topology == "front_door" else "",
                auth_db=str(auth_db),
                svc_group=gs.SVC_GROUP,
                cap=_CAP,
                python=sys.executable,
                server=str(_MATH_SERVER),
            )
            hangar = stack.enter_context(running_hangar(workdir, config))
            served[topology] = _Gateway(base_url=hangar.base_url, api_key=keys[_TENANT])
        yield served


def _hangar_call(gateway: _Gateway, arguments: dict[str, Any]) -> dict[str, Any]:
    is_error, text = gateway.call(
        "hangar_call", {"calls": [{"mcp_server": "math", "tool": "add", "arguments": arguments}]}
    )
    assert not is_error, text
    (call,) = json.loads(text)["results"]
    return dict(call)


def test_a_payload_over_the_cap_is_refused_on_the_flat_path_with_the_hangar_call_error(
    gateways: dict[str, _Gateway],
) -> None:
    refused = _hangar_call(gateways["egress"], _OVER)
    flat_is_error, flat_text = gateways["front_door"].call("add", _OVER)

    assert refused["success"] is False and refused["error_type"] == "ValidatorDenied", refused
    assert f"exceeds cap {_CAP}" in refused["error"], refused
    assert flat_is_error, f"the flat call was served: {flat_text!r}"
    assert flat_text == refused["error"]


def test_a_payload_under_the_cap_is_served_on_the_flat_path(gateways: dict[str, _Gateway]) -> None:
    is_error, text = gateways["front_door"].call("add", _UNDER)

    assert not is_error, text
    assert "3" in text
