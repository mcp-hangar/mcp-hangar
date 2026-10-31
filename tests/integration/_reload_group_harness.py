"""Boot Hangar, make groups and members over REST, reload, and read what the groups are after it (#2169).

Run as a script, in its own interpreter, by ``test_a_reload_keeps_groups_made_over_rest.py``:
``python _reload_group_harness.py <egress|front_door> <workdir> <out.json>``, and by
``test_a_restart_keeps_groups_made_over_rest.py`` with ``restart:<backend>:<phase>``.
Not collected by pytest. A separate process because ``bootstrap()`` fills
process-global state, and because a restart is the thing tested: each phase is a
gateway of its own on the same working directory.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, REST calls and MCP calls, against real stdio subprocess servers.

``pool`` is a group the file declares, and it denies ``add``. ``runtime`` is a
group created over REST, with a policy of its own that denies ``add``. ``doomed``
is a group the file declares and the reload's file does not. ``front_door`` runs
``pool`` alone: its one member from the file, and one added over REST.

The ``restart`` phases, in the order the test runs them (#2172):

* ``made``: makes groups and members over REST, removes some of them over REST,
  and reads what a reload leaves.
* ``restarted``: the same file. Reads the groups and the resolver before anything
  is served, and its first request is a call that group policy refuses.
* ``file_takes``: the file now names ``kept-member`` and declares ``runtime``.
* ``again``: the same file, to show a further restart changes nothing.
* ``file_drops``: the file names neither.
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


def _gateway_config(mode: str, servers: dict[str, Any], *, durable_auth: bool = False) -> dict[str, Any]:
    auth = {
        "enabled": True,
        "allow_anonymous": False,
        "api_key": {"enabled": True},
        "role_assignments": [{"principal": PRINCIPAL, "role": "admin", "scope": "global"}],
    }
    return {
        "tool_access": {"mode": mode},
        "mcp_servers": servers,
        # Left out, the selected backend keeps the keys and the group policies too.
        "auth": auth if durable_auth else {**auth, "storage": {"driver": "memory"}},
        "persistence": {"backend": "sqlite", "sqlite": {"data_dir": "data"}},
        "config_reload": {"enabled": False},
    }


def _restart_config(backend: str, phase: str) -> dict[str, Any]:
    named = {"id": "file-member"}
    pool = _group(named, tools=DENY_ADD)
    servers: dict[str, Any] = {"file-member": _server(), "pool": pool}
    if phase in ("file_takes", "again"):
        pool["members"] = [named, {"id": "kept-member", "weight": 7}]
        servers["kept-member"] = _server()
        servers["runtime"] = {"mode": "group", "auto_start": False, "strategy": "random", "members": []}
    mode = "front_door" if backend == "front_door" else "egress"
    config = _gateway_config(mode, servers, durable_auth=backend != "memory")
    if backend == "memory":
        del config["persistence"]
    return config


def _shape(group_map: dict[str, Any]) -> dict[str, Any]:
    """What a group is, leaving out the rotation, which a process has of its own."""
    return {
        group_id: {
            "strategy": str(group.strategy.value),
            "min_healthy": group.to_status_dict().get("min_healthy"),
            "description": group.description,
            "members": {str(m.id): [m.weight, m.priority] for m in group.members},
        }
        for group_id, group in group_map.items()
    }


def _governed(group_map: dict[str, Any]) -> dict[str, list[str]]:
    """What the group policy in force denies each member, read before anything is served."""
    from mcp_hangar.domain.services import get_tool_access_resolver

    resolver = get_tool_access_resolver()
    return {
        str(member.id): sorted(getattr(resolver.get_configured_policy("group", group_id), "deny_list", ()))
        for group_id, group in group_map.items()
        for member in group.members
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


def _front_door_made(gateway: _Gateway) -> dict[str, Any]:
    gateway.create("new-server")
    gateway.add_member("pool", "new-server")
    return {}


def _made(gateway: _Gateway, reload: Any, *, durable_auth: bool) -> dict[str, Any]:
    for mcp_server_id in ("kept-member", "new-server", "runtime-member", "gone-member", "removed", "in-deleted"):
        gateway.create(mcp_server_id)
    gateway.must(
        "POST", "/groups", {"group_id": "runtime", "strategy": "priority", "min_healthy": 2, "description": "from REST"}
    )
    if durable_auth:
        gateway.must("POST", "/auth/policies/group/runtime", DENY_ADD)
    gateway.must("POST", "/groups", {"group_id": "deleted"})
    gateway.must("POST", "/groups/pool/members", {"member_id": "kept-member", "weight": 3, "priority": 2})
    gateway.add_member("pool", "new-server")
    for mcp_server_id in ("runtime-member", "gone-member", "removed"):
        gateway.add_member("runtime", mcp_server_id)
    gateway.add_member("deleted", "in-deleted")
    # Each of these writes through, so none of it comes back.
    gateway.must("PUT", "/groups/runtime", {"strategy": "weighted_round_robin"})
    gateway.must("DELETE", "/groups/runtime/members/removed")
    gateway.must("DELETE", "/groups/deleted")
    gateway.must("DELETE", "/mcp_servers/gone-member")
    status = reload()
    return {"status": status, "groups": _shape(gateway_groups())}


def gateway_groups() -> dict[str, Any]:
    from mcp_hangar.server.state import GROUPS

    return GROUPS


def _restart_phase(backend: str, phase: str, workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    config = workdir / "config.yaml"
    _write(config, _restart_config(backend, phase))
    context = bootstrap(config_path=str(config))
    repository = context.runtime.repository
    # Before anything can serve: bootstrap has returned, and neither the app, the
    # workers nor the tail has started.
    out: dict[str, Any] = {
        "at_boot": _shape(gateway_groups()),
        "governed_at_boot": _governed(gateway_groups()),
        "workers_running": [str(w) for w in context.background_workers if getattr(w, "running", False)],
    }
    key = context.auth_components.api_key_store.create_key(principal_id=PRINCIPAL, name=f"op-{phase}", tenant_id=TENANT)
    front_door = backend == "front_door"
    with TestClient(_served_app(context, ServerLifecycle(context)), base_url=BASE_URL) as client:
        gateway = _Gateway(client, {"op": key})
        reload = lambda: gateway.reload("op")[0]  # noqa: E731
        if phase == "made" and front_door:
            out.update(_front_door_made(gateway))
        elif phase == "made":
            out.update(_made(gateway, reload, durable_auth=backend != "memory"))
        elif front_door:
            gateway.must("POST", "/mcp_servers/new-server/start", {})  # A running member lists its tools.
            out["first_listing_has_add"] = "add" in gateway.listed()
            answer = gateway.flat_call()
            out["flat_add_refused"] = bool("error" in answer or answer.get("result", {}).get("isError", False))
            out["other_tools_listed"] = "echo" in gateway.listed()
        else:
            if "new-server" in repository.get_all():
                out["first_call"] = gateway.call("new-server")
            out["reloaded"] = {"status": reload(), "groups": _shape(gateway_groups())}
            out["ids"] = sorted(repository.get_all())
            if "runtime" in gateway_groups():
                out["through_the_group"] = _echo(gateway, "runtime")
                out["policy"] = gateway.send("GET", "/auth/policies/group/runtime").json().get("deny_list")
    for server in repository.get_all().values():
        server.shutdown()
    return out


def _echo(gateway: _Gateway, group_id: str) -> str:
    calls = [{"mcp_server": group_id, "tool": "echo", "arguments": {"message": "hi"}}]
    payload = gateway.rpc("op", "tools/call", {"name": "hangar_call", "arguments": {"calls": calls}})
    (result,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return "served" if result["success"] else str(result.get("error_type"))


def run(mode: str, workdir: Path) -> dict[str, Any]:
    if mode.startswith("restart:"):
        _, backend, phase = mode.split(":")
        return _restart_phase(backend, phase, workdir)

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
