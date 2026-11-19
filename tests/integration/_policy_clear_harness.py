"""Boot Hangar, set and clear tool access policies over REST, and call between each step (#2178).

Run as a script, in its own interpreter, by
``test_a_cleared_tool_access_policy_applies_at_once.py``:
``python _policy_clear_harness.py <workdir> <out.json> change|restart``. Not
collected by pytest. A separate process because ``bootstrap()`` fills
process-global state; ``restart`` boots a second one on the first one's data.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, REST and MCP calls under an API key, real stdio subprocess servers.

* ``declared`` has a policy in the file, denying ``add``.
* ``plain`` has none.
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
TARGETS = ("declared", "plain")
TOOLS = ("add", "echo")


def _server(**extra: Any) -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600, **extra}


def _config() -> dict[str, Any]:
    return {
        "mcp_servers": {
            "declared": _server(tools={"deny_list": ["add"]}),
            "plain": _server(),
        },
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
        (result,) = body["results"]
        return "served" if result["success"] else str(result.get("error_type"))

    def surface(self) -> dict[str, dict[str, str]]:
        """Every target and tool, each call resolving (and caching) the caller's policy first."""
        return {target: {tool: self.call(target, tool) for tool in TOOLS} for target in TARGETS}


def run(workdir: Path, step: str) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    config = workdir / "config.yaml"
    _write(config, _config())
    context = bootstrap(config_path=str(config))
    repository = context.runtime.repository
    key = context.auth_components.api_key_store.create_key(principal_id=PRINCIPAL, name=f"op-{step}")
    out: dict[str, Any] = {}
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Gateway(client, {"op": key})
        for mcp_server_id in TARGETS:
            gateway.must("POST", f"/mcp_servers/{mcp_server_id}/start", {})

        if step == "change":
            out["before"] = gateway.surface()
            for target in TARGETS:
                gateway.must("POST", f"/auth/policies/provider/{target}", {"deny_list": ["echo"]})
            out["after_set"] = gateway.surface()
            for target in TARGETS:
                gateway.must("DELETE", f"/auth/policies/provider/{target}")
            out["after_clear"] = gateway.surface()
            # A second clear of a row that is gone changes nothing.
            gateway.must("DELETE", "/auth/policies/provider/declared")
            out["after_second_clear"] = gateway.surface()
        else:
            out["after_restart"] = gateway.surface()
    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(workdir: Path, out: Path, step: str) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(workdir, step)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
