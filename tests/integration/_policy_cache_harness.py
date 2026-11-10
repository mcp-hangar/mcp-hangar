"""Boot Hangar, call, change a server-scope policy over REST, and call again (#2203).

Run as a script, in its own interpreter, by
``test_a_server_policy_set_over_rest_reaches_a_cached_scope.py``:
``python _policy_cache_harness.py <workdir> <out.json>``. Not collected by
pytest. A separate process because ``bootstrap()`` fills process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, REST calls and MCP calls under an API key that carries a tenant,
against real stdio subprocess servers. Each call goes through ``hangar_call``,
so each one resolves, and caches, the caller's effective policy first.

* ``pool`` is a group with the member ``member``. A provider policy set on the
  group id is read by every scope of the group branch, which is asked with the
  group id as its server id.
* ``_global`` is the policy every scope reads as a floor.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from _reload_served_harness import BASE_URL, _Client, _served_app, _write

TESTS = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, str(TESTS / "mock_provider.py")]
PRINCIPAL = "svc:operator"
TENANT = "tenant-a"
TARGETS = ("plain", "member", "pool")


def _server() -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600}


def _config() -> dict[str, Any]:
    return {
        "mcp_servers": {
            "plain": _server(),
            "member": _server(),
            "pool": {"mode": "group", "auto_start": False, "members": [{"id": "member"}]},
        },
        # No `storage`: the selected backend keeps the keys and the policies, so
        # the REST policy endpoint has the durable store it needs.
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True},
            "role_assignments": [{"principal": PRINCIPAL, "role": "admin", "scope": "global"}],
        },
        "persistence": {"backend": "sqlite", "sqlite": {"data_dir": "data"}},
        "config_reload": {"enabled": False},
    }


class _Gateway(_Client):
    def must(self, method: str, path: str, body: Any = None) -> Any:
        answer = self.client.request(method, "/api" + path, headers={"X-API-Key": self.keys["op"]}, json=body)
        assert answer.status_code < 300, f"{method} {path}: {answer.text}"
        return answer

    def call(self, mcp_server: str, tool: str) -> str:
        arguments = {"message": "hi"} if tool == "echo" else {"a": 1, "b": 2}
        calls = [{"mcp_server": mcp_server, "tool": tool, "arguments": arguments}]
        payload = self.rpc("op", "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})
        body = json.loads(payload["result"]["content"][0]["text"])
        if "results" not in body:
            return "invalid: " + "; ".join(error["message"] for error in body["validation_errors"])
        (result,) = body["results"]
        return "served" if result["success"] else str(result.get("error_type"))

    def surface(self, tool: str) -> dict[str, str]:
        return {target: self.call(target, tool) for target in TARGETS}


def run(workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    config = workdir / "config.yaml"
    _write(config, _config())
    context = bootstrap(config_path=str(config))
    repository = context.runtime.repository
    key = context.auth_components.api_key_store.create_key(principal_id=PRINCIPAL, name="op", tenant_id=TENANT)
    out: dict[str, Any] = {}
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Gateway(client, {"op": key})
        for mcp_server_id in ("plain", "member"):
            gateway.must("POST", f"/mcp_servers/{mcp_server_id}/start", {})

        # Each surface is read once before the change, so the change meets a cache.
        out["add_before"] = gateway.surface("add")
        gateway.must("POST", "/auth/policies/provider/pool", {"deny_list": ["add"]})
        out["add_after_group_id_policy"] = gateway.surface("add")

        out["echo_before"] = gateway.surface("echo")
        gateway.must("POST", "/auth/policies/provider/_global", {"deny_list": ["echo"]})
        out["echo_after_global_policy"] = gateway.surface("echo")
    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
