"""Boot Hangar, make groups and members over REST, reload, and read what the groups are after it (#2169).

Run as a script, in its own interpreter, by ``test_a_reload_keeps_groups_made_over_rest.py``:
``python _reload_group_harness.py <egress|front_door> <workdir> <out.json>``.
Not collected by pytest. A separate process because ``bootstrap()`` fills
process-global state.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, REST calls and MCP calls, against real stdio subprocess servers.

``pool`` is a group the file declares, and it denies ``add``. ``runtime`` is a
group created over REST, with a policy of its own that denies ``add``. ``doomed``
is a group the file declares and the reload's file does not. ``front_door`` runs
``pool`` alone: its one member from the file, and one added over REST.
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
DENY_ADD = {"deny_list": ["add"]}


def _server(**extra: Any) -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600, **extra}


def _group(*members: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"mode": "group", "auto_start": False, "members": list(members), **extra}


def _config(phase: str, mode: str) -> dict[str, Any]:
    if mode == "front_door":  # one group and nothing else: a tool listed would be the member's
        servers = {"file-member": _server(), "pool": _group({"id": "file-member"}, tools=DENY_ADD)}
        return _gateway_config(mode, servers)
    servers: dict[str, Any] = {
        "file-member": _server(),
        "was-a-file-member": _server(),
        "edited": _server(env={"VERSION": "one" if phase == "boot" else "two"}),
    }
    if phase == "boot":
        servers["leaves"] = _server()
        servers["pool"] = _group({"id": "file-member"}, {"id": "was-a-file-member"}, tools=DENY_ADD)
        servers["doomed"] = _group({"id": "file-member"})
    else:
        servers["declared-later"] = _server()
        servers["pool"] = _group({"id": "file-member"}, {"id": "declared-later", "weight": 5}, tools=DENY_ADD)
    return _gateway_config(mode, servers)


def _gateway_config(mode: str, servers: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_access": {"mode": mode},
        "mcp_servers": servers,
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True},
            "storage": {"driver": "memory"},
            "role_assignments": [{"principal": PRINCIPAL, "role": "admin", "scope": "global"}],
        },
        "persistence": {"backend": "sqlite", "sqlite": {"data_dir": "data"}},
        "config_reload": {"enabled": False},
    }


class _Gateway(_Client):
    def send(self, method: str, path: str, body: Any = None) -> Any:
        return self.client.request(method, "/api" + path, headers={"X-API-Key": self.keys["op"]}, json=body)

    def must(self, method: str, path: str, body: Any = None) -> Any:
        answer = self.send(method, path, body)
        assert answer.status_code < 300, f"{method} {path}: {answer.text}"
        return answer

    def add_member(self, group_id: str, mcp_server_id: str, weight: int = 1) -> None:
        self.must("POST", f"/groups/{group_id}/members", {"member_id": mcp_server_id, "weight": weight})

    def create(self, mcp_server_id: str) -> None:
        self.must("POST", "/mcp_servers", {"mcp_server_id": mcp_server_id, "mode": "subprocess", "command": COMMAND})

    def call(self, mcp_server: str) -> str:
        calls = [{"mcp_server": mcp_server, "tool": "add", "arguments": {"a": 1, "b": 2}}]
        payload = self.rpc("op", "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})
        body = json.loads(payload["result"]["content"][0]["text"])
        if "results" not in body:
            return "invalid: " + "; ".join(error["message"] for error in body["validation_errors"])
        (result,) = body["results"]
        return "served" if result["success"] else str(result.get("error_type"))

    def listed(self) -> list[str]:
        return sorted(tool["name"] for tool in self.rpc("op", "tools/list", {})["result"]["tools"])

    def flat_call(self) -> Any:
        return self.rpc("op", "tools/call", {"name": "add", "arguments": {"a": 1, "b": 2}})

    def surface(self, mode: str) -> dict[str, Any]:
        if mode == "front_door":
            answer = self.flat_call()
            refused = "error" in answer or answer.get("result", {}).get("isError", False)
            return {"listed_add": "add" in self.listed(), "flat_add_refused": bool(refused)}
        return {sid: self.call(sid) for sid in ("kept-member", "runtime-member", "file-member", "pool", "runtime")}

    def front_door(self, repository: Any, reload: Any) -> dict[str, Any]:
        """A member added over REST, on the flat surface, before and after a reload."""
        self.create("kept-member")
        self.add_member("pool", "kept-member")
        self.must("POST", "/mcp_servers/kept-member/start", {})
        before = {"groups": _groups(repository), "surface": self.surface("front_door")}
        status = reload()
        return {
            "before": before,
            "status": status,
            "groups": _groups(repository),
            "surface": self.surface("front_door"),
        }


def _groups(repository: Any) -> dict[str, Any]:
    from mcp_hangar.server.state import GROUPS

    return {
        group_id: {
            "strategy": str(group.strategy.value),
            "members": {
                str(member.id): {
                    "weight": member.weight,
                    "holds_the_running_server": member.mcp_server is repository.get(str(member.id)),
                }
                for member in group.members
            },
            "route_candidates": sorted(str(server.id) for server in group.route_candidates(TENANT)),
        }
        for group_id, group in GROUPS.items()
    }


def run(mode: str, workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    config = workdir / "config.yaml"
    _write(config, _config("boot", mode))
    context = bootstrap(config_path=str(config))
    repository = context.runtime.repository
    key = context.auth_components.api_key_store.create_key(principal_id=PRINCIPAL, name="op", tenant_id=TENANT)
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Gateway(client, {"op": key})
        if mode == "front_door":
            front_door = gateway.front_door(repository, lambda: gateway.reload("op")[0])
            for server in repository.get_all().values():
                server.shutdown()
            return front_door
        for mcp_server_id in ("kept-member", "runtime-member", "declared-later", "doomed-member"):
            gateway.create(mcp_server_id)
        gateway.must("POST", "/groups", {"group_id": "runtime", "strategy": "priority"})
        gateway.must("POST", "/auth/policies/group/runtime", DENY_ADD)

        gateway.add_member("pool", "kept-member", weight=3)
        gateway.add_member("pool", "declared-later", weight=2)
        gateway.add_member("doomed", "doomed-member")
        for mcp_server_id in ("runtime-member", "leaves", "edited"):
            gateway.add_member("runtime", mcp_server_id)
        for mcp_server_id in ("kept-member", "runtime-member", "doomed-member"):
            gateway.must("POST", f"/mcp_servers/{mcp_server_id}/start", {})
        running = {sid: repository.get(sid) for sid in ("kept-member", "runtime-member", "doomed-member")}

        before = {"groups": _groups(repository), "surface": gateway.surface(mode)}
        _write(config, _config("reloaded", mode))
        status, _ = gateway.reload("op")
        out = {
            "before": before,
            "status": status,
            "groups": _groups(repository),
            "surface": gateway.surface(mode),
            "same_server": {sid: repository.get(sid) is server for sid, server in running.items()},
            "running": {sid: server._client.is_alive() for sid, server in running.items()},
            "group_policy": gateway.send("GET", "/auth/policies/group/runtime").json().get("deny_list"),
            "ids": sorted(repository.get_all()),
        }
        status, _ = gateway.reload("op")
        out["second_reload"] = {"status": status, "groups": _groups(repository)}
    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(mode: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(mode, workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
