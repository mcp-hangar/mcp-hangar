"""Tier 0 live verification: a reload keeps a front door a front door (#1424).

A reload reset the topology mode to ``egress`` and applied only ``mcp_servers``.
On a running ``front_door`` gateway, an API key issued without a tenant listed
no tools and was answered ``-32601`` -- until any reload, even of an unchanged
file, after which it listed every tool and its calls were served.

Black-box, against the ``mcp-hangar`` this checkout installs (the one next to
the running interpreter, checked to import this working tree's code by
``tests/_hangar_executable.py``), over real
streamable HTTP. Auth is on, anonymous is refused, and the keys are seeded with
the shipped ``SQLiteApiKeyStore``. Each trigger reloads the same file:
``POST /api/config/reload``, SIGHUP, and the config file watcher. A file that
switches to ``egress`` is refused with 409 and changes nothing.

The in-process version, which CI runs on every PR, is
``tests/integration/test_a_reload_is_served_whole.py``.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t0_reload_keeps_the_front_door.py -m "live and t0"
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any

import httpx
import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar, RunningHangar

pytestmark = [pytest.mark.live, pytest.mark.t0]

MODERN = "2026-07-28"
ENVELOPE = {
    "io.modelcontextprotocol/protocolVersion": MODERN,
    "io.modelcontextprotocol/clientInfo": {"name": "reload-live", "version": "0"},
    "io.modelcontextprotocol/clientCapabilities": {},
}
METHOD_NOT_FOUND = -32601
TOOL = "add"

_CONFIG = """\
logging:
  level: INFO
tool_access:
  mode: {mode}
config_reload:
  enabled: true
  interval_s: 1
  use_watchdog: false
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
    - principal: "group:admins"
      role: admin
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 600
"""


def _seed_keys(auth_db: Path) -> dict[str, str]:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        return {
            "tenant": store.create_key(
                principal_id="svc:tenant-a", name="k-a", tenant_id="tenant:a", groups=frozenset({"svc-callers"})
            ),
            # Holds tool:invoke, and carries no tenant: the caller a front door denies.
            "no_tenant": store.create_key(principal_id="svc:no-tenant", name="k-nt", groups=frozenset({"svc-callers"})),
            "admin": store.create_key(principal_id="ops:admin", name="k-admin", groups=frozenset({"admins"})),
        }
    finally:
        store.close()


@dataclass
class _Gateway:
    hangar: RunningHangar
    keys: dict[str, str]
    config_path: Path
    config_text: str

    def rpc(self, who: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MODERN,
            "Mcp-Method": method,
            "X-API-Key": self.keys[who],
        }
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": ENVELOPE}}
        response = httpx.post(f"{self.hangar.base_url}/mcp", headers=headers, content=json.dumps(body), timeout=30)
        text = response.text.lstrip()
        if not text.startswith("{"):
            text = next(line[len("data: ") :] for line in text.splitlines() if line.startswith("data: "))
        return dict(json.loads(text))

    def tools(self, who: str) -> list[str]:
        return sorted(tool["name"] for tool in self.rpc(who, "tools/list", {})["result"]["tools"])

    def call(self, who: str) -> Any:
        payload = self.rpc(who, "tools/call", {"name": TOOL, "arguments": {"a": 1, "b": 2}})
        return payload["error"]["code"] if "error" in payload else "served"

    def seen(self) -> dict[str, Any]:
        return {
            "no_tenant_tools": self.tools("no_tenant"),
            "no_tenant_call": self.call("no_tenant"),
            "tenant_call": self.call("tenant"),
        }

    def reload_over_rest(self) -> httpx.Response:
        return httpx.post(
            f"{self.hangar.base_url}/api/config/reload",
            headers={"Content-Type": "application/json", "X-API-Key": self.keys["admin"]},
            content="{}",
            timeout=60,
        )

    def reloads(self, outcome: str = "configuration_reloaded") -> int:
        return self.hangar.log_path.read_text(errors="replace").count(outcome)

    def wait_for_reload(self, before: int, outcome: str = "configuration_reloaded") -> None:
        deadline = time.monotonic() + 30
        while self.reloads(outcome) <= before:
            if time.monotonic() > deadline:
                pytest.fail(f"no {outcome} in 30s:\n{self.hangar.output()[-2000:]}")
            time.sleep(0.2)


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("reload_front_door")
    keys = _seed_keys(workdir / "auth.db")
    text = _CONFIG.format(mode="front_door", auth_db=workdir / "auth.db", python=sys.executable, server=_MATH_SERVER)
    with running_hangar(workdir, text) as hangar:
        gw = _Gateway(hangar, keys, workdir / "config.yaml", text)
        deadline = time.monotonic() + 40
        while TOOL not in gw.tools("tenant"):
            if time.monotonic() > deadline:
                pytest.skip(f"the tenant never saw {TOOL!r}:\n{hangar.output()[-2000:]}")
            time.sleep(0.5)
        yield gw


CLOSED = {"no_tenant_tools": [], "no_tenant_call": METHOD_NOT_FOUND, "tenant_call": "served"}


def test_a_key_without_a_tenant_sees_nothing_before_any_reload(gateway: _Gateway) -> None:
    assert gateway.seen() == CLOSED


def test_and_after_a_rest_reload_of_the_same_file(gateway: _Gateway) -> None:
    before = gateway.reloads()

    response = gateway.reload_over_rest()

    assert response.status_code == 200, response.text
    gateway.wait_for_reload(before)
    assert gateway.seen() == CLOSED


def test_and_after_sighup(gateway: _Gateway) -> None:
    before = gateway.reloads()

    os.kill(gateway.hangar.proc.pid, signal.SIGHUP)

    gateway.wait_for_reload(before)
    assert gateway.seen() == CLOSED


def test_and_after_the_file_watcher_sees_a_byte_identical_save(gateway: _Gateway) -> None:
    before = gateway.reloads()

    stamp = gateway.config_path.stat().st_mtime + 5
    os.utime(gateway.config_path, (stamp, stamp))

    gateway.wait_for_reload(before)
    assert gateway.seen() == CLOSED


def test_a_file_that_switches_to_egress_is_refused_and_changes_nothing(gateway: _Gateway) -> None:
    refused_before = gateway.reloads("configuration_reload_failed")
    gateway.config_path.write_text(gateway.config_text.replace("mode: front_door", "mode: egress"))
    try:
        response = gateway.reload_over_rest()

        assert response.status_code == 409, response.text
        assert "Restart the gateway" in response.text
        gateway.wait_for_reload(refused_before, "configuration_reload_failed")
        assert gateway.seen() == CLOSED
    finally:
        # The watcher sees this save too and reloads the front_door file again.
        gateway.config_path.write_text(gateway.config_text)
