"""Tier 0 live verification: a front door lists the tools of a member its group started at load (#2257).

A group with ``auto_start`` (the default) starts its members while the
configuration loads, before the projection handler subscribes to
``McpServerStarted``. Bootstrap now publishes those starts once its handlers
are registered, so the member is projected at boot. On a front door the boot
warm-up's start command also publishes whatever a server left recorded, so this
listing held the member's tools before the fix as well; the egress tier is where
the defect showed (``test_t0_hangar_call_header_exposure.py``). This pins that
the flat listing keeps them now that the start is published at boot instead.

Black-box, against the ``mcp-hangar`` this checkout installs, over real
streamable HTTP with API-key auth and a tenant key. ``pool`` names its tools in
``tool_projection.flat``, as a front door requires, and ``pooled`` is its one
member, defined inline.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_front_door_member_started_at_load.py -m "live and t0"
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar
from tests.live.test_t0_reload_keeps_the_front_door import _Gateway, _seed_keys

pytestmark = [pytest.mark.live, pytest.mark.t0]

FLAT = ["add", "divide", "multiply", "power", "subtract"]

_CONFIG = """\
logging:
  level: INFO
tool_access:
  mode: front_door
config_reload:
  enabled: false
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
    - principal: "group:svc-callers"
      role: service-account
      scope: global
mcp_servers:
  pool:
    mode: group
    strategy: round_robin
    tool_projection:
      flat: [add, subtract, multiply, divide, power]
    members:
      - id: pooled
        mode: subprocess
        command: ["{python}", "{server}"]
        idle_ttl_s: 600
"""


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("front_door_member_at_load")
    keys = _seed_keys(workdir / "auth.db")
    text = _CONFIG.format(auth_db=workdir / "auth.db", python=sys.executable, server=_MATH_SERVER)
    with running_hangar(workdir, text) as hangar:
        yield _Gateway(hangar, keys, workdir / "config.yaml", text)


def test_the_flat_listing_holds_the_members_tools(gateway: _Gateway) -> None:
    deadline = time.monotonic() + 40
    listed: list[str] = []
    while time.monotonic() < deadline:
        listed = [name for name in gateway.tools("tenant") if not name.startswith("hangar_")]
        if listed:
            break
        time.sleep(0.5)
    assert listed == FLAT, gateway.hangar.output()[-2000:]


def test_a_listed_member_tool_is_served(gateway: _Gateway) -> None:
    payload = gateway.rpc("tenant", "tools/call", {"name": "add", "arguments": {"a": 1, "b": 2}})
    assert "error" not in payload, payload
    assert payload["result"].get("isError") is not True, payload
