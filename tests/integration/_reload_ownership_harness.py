"""Boot Hangar from a file, reload it, and read which servers the reload left (#2169).

Run as a script, in its own interpreter, by ``test_a_reload_removes_only_what_the_file_owns.py``:
``python _reload_ownership_harness.py <phase> <sqlite|memory> <workdir> <out.json>``.
Not collected by pytest. A separate process because ``bootstrap()`` fills
process-global state, and because a restart is the thing being compared: each
phase is a gateway of its own on the same working directory.

What runs is production: ``bootstrap(config_path=...)``, the app ``serve --http``
serves, and only REST calls an operator can make. ``found-one`` comes from a
configured filesystem source, by a scan and an approval; ``crud-one`` from
``POST /api/mcp_servers``. Both are real stdio subprocesses.

The phases, in the order the test runs them on one directory:

* ``first``: the file declares ``upstream``, ``gone`` and the group ``pool``,
  with two inline members. Adds both non-file servers, with policies, and
  reloads a file without ``gone`` or the member ``gone-member``.
* ``restart``: boots that same file, reloads it, and scans.
* ``takeover``: the file now declares ``crud-one`` and ``found-one``.
* ``after_takeover``: boots that file.
* ``dropped``: the file stops declaring ``crud-one`` again.
* ``after_dropped``: boots that file.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from _reload_served_harness import BASE_URL, _served_app, _write

from mcp_hangar.domain.services import get_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

TESTS = Path(__file__).resolve().parents[1]
MOCK_PROVIDER = TESTS / "mock_provider.py"
PRINCIPAL = "svc:operator"
COMMAND = [sys.executable, str(MOCK_PROVIDER)]
FROM_THE_FILE = "as the file says"


def _server(**extra: Any) -> dict[str, Any]:
    return {"mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600, **extra}


def _config(backend: str, servers: dict[str, Any], *, members: tuple[str, ...] = ("member",)) -> dict[str, Any]:
    pool = {"mode": "group", "auto_start": False, "members": [{"id": sid, **_server()} for sid in members]}
    config: dict[str, Any] = {
        "mcp_servers": {"upstream": _server(description=FROM_THE_FILE), "pool": pool, **servers},
        "discovery": {
            "enabled": True,
            "auto_register": False,
            "sources": [{"type": "filesystem", "mode": "additive", "path": "sources", "watch": False}],
        },
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True},
            "storage": {"driver": "memory"},
            "role_assignments": [{"principal": PRINCIPAL, "role": "admin", "scope": "global"}],
        },
        "config_reload": {"enabled": False},
    }
    if backend == "sqlite":
        config["persistence"] = {"backend": "sqlite", "sqlite": {"data_dir": "data"}}
    return config


#: What each phase's file declares, on top of `upstream` and `pool`.
TAKEN_OVER = {"crud-one": _server(description=FROM_THE_FILE), "found-one": _server(description=FROM_THE_FILE)}
FILES: dict[str, dict[str, Any]] = {
    "boot": {"gone": _server()},
    "reloaded": {},
    "taken_over": TAKEN_OVER,
    "dropped": {"found-one": TAKEN_OVER["found-one"]},
}


def _write_definition(workdir: Path, description: str = "") -> None:
    directory = workdir / "sources"
    directory.mkdir(exist_ok=True)
    connection = {"command": COMMAND, "env": {"VARIANT": description}} if description else {"command": COMMAND}
    definition = {"name": "found-one", "mode": "subprocess", "connection": connection}
    (directory / "found-one.yaml").write_text(json.dumps(definition))  # JSON is YAML


class _Operator:
    """One gateway, as the REST API shows it."""

    def __init__(self, client: Any, key: str, repository: Any) -> None:
        self.client = client
        self.headers = {"X-API-Key": key}
        self.repository = repository

    def send(self, method: str, path: str, body: Any = None) -> Any:
        return self.client.request(method, "/api" + path, headers=self.headers, json=body)

    def ids(self) -> list[str]:
        answer = self.send("GET", "/mcp_servers").json()
        return sorted(server["mcp_server_id"] for server in answer["mcp_servers"])

    def description(self, mcp_server_id: str) -> str | None:
        return self.repository.get(mcp_server_id).description

    def reload(self) -> dict[str, Any]:
        response = self.send("POST", "/config/reload", {})
        assert response.status_code == 200, response.text
        return dict(response.json()["result"])

    def scan(self) -> dict[str, Any]:
        from mcp_hangar.domain.value_objects.discovery import config_source_id

        scan = self.send("POST", f"/discovery/sources/{config_source_id('filesystem')}/scan")
        pending = self.send("GET", "/discovery/pending").json()
        return {
            "status": scan.status_code,
            "found": scan.json().get("mcp_servers_found"),
            "pending": sorted(entry["name"] for entry in pending["pending"]),
            "ids": self.ids(),
        }

    def start(self, mcp_server_id: str) -> Any:
        response = self.send("POST", f"/mcp_servers/{mcp_server_id}/start", {})
        assert response.status_code == 200, response.text
        return self.repository.get(mcp_server_id)._client.process

    def set_policies(self, mcp_server_id: str, backend: str) -> None:
        if backend == "sqlite":
            answer = self.send("POST", f"/auth/policies/provider/{mcp_server_id}", {"deny_list": ["add"]})
            assert answer.status_code == 200, answer.text
        else:  # the REST endpoint needs a durable store; what it sets at runtime is this
            get_tool_access_resolver().set_mcp_server_policy(mcp_server_id, ToolAccessPolicy(deny_list=("add",)))
        answer = self.send("POST", f"/mcp_servers/{mcp_server_id}/l7_policy", {"defaultAction": "allow"})
        assert answer.status_code == 200, answer.text

    def policies(self, mcp_server_id: str) -> dict[str, Any]:
        access = get_tool_access_resolver().get_configured_policy("provider", mcp_server_id)
        l7 = self.send("GET", f"/mcp_servers/{mcp_server_id}/l7_policy")
        return {
            "deny_list": list(access.deny_list) if access is not None else None,
            "l7_action": l7.json().get("defaultAction") if l7.status_code == 200 else None,
        }


def _exited(process: Any) -> bool:
    deadline = time.monotonic() + 10
    while process.poll() is None:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _groups() -> dict[str, list[str]]:
    from mcp_hangar.server.state import GROUPS

    return {group_id: sorted(member.id for member in group.members) for group_id, group in GROUPS.items()}


def _first(gateway: _Operator, config: Path, backend: str) -> dict[str, Any]:
    created = gateway.send(
        "POST",
        "/mcp_servers",
        {"mcp_server_id": "crud-one", "mode": "subprocess", "command": COMMAND, "idle_ttl_s": 600},
    )
    assert created.status_code == 201, created.text
    scanned = gateway.scan()
    approved = gateway.send("POST", "/discovery/approve/found-one", {})
    assert approved.status_code == 200, approved.text

    for mcp_server_id in ("crud-one", "found-one"):
        gateway.set_policies(mcp_server_id, backend)

    # An edit of a server the file owns: the file's definition still wins.
    edited = gateway.send("PUT", "/mcp_servers/upstream", {"description": "edited at runtime"})
    assert edited.status_code == 200, edited.text

    # A non-file server in a group the file declares, and in one it does not.
    for group_id in ("pool", "runtime-group"):
        if group_id != "pool":
            group = gateway.send("POST", "/groups", {"group_id": group_id})
            assert group.status_code == 201, group.text
        member = gateway.send("POST", f"/groups/{group_id}/members", {"member_id": "crud-one"})
        assert member.status_code in (200, 201), member.text

    processes = {sid: gateway.start(sid) for sid in ("crud-one", "found-one")}
    gone = gateway.start("gone")
    objects = {sid: gateway.repository.get(sid) for sid in processes}
    before = {
        "ids": gateway.ids(),
        "groups": _groups(),
        "policies": {sid: gateway.policies(sid) for sid in processes},
    }

    _write(config, _config(backend, FILES["reloaded"]))
    result = gateway.reload()
    return {
        "first_scan": scanned,
        "before": before,
        "result": {key: value for key, value in result.items() if key.startswith("mcp_servers_")},
        "ids": gateway.ids(),
        "groups": _groups(),
        "same_object": {sid: gateway.repository.get(sid) is server for sid, server in objects.items()},
        "running": {sid: process.poll() is None for sid, process in processes.items()},
        "states": {sid: str(server.state) for sid, server in objects.items()},
        "policies": {sid: gateway.policies(sid) for sid in processes},
        "gone_exited": _exited(gone),
        "upstream_description": gateway.description("upstream"),
        "rescan": gateway.scan(),
        "running_after_rescan": {sid: process.poll() is None for sid, process in processes.items()},
        "same_object_after_rescan": {sid: gateway.repository.get(sid) is server for sid, server in objects.items()},
    }


def _restart(gateway: _Operator) -> dict[str, Any]:
    booted = {"ids": gateway.ids(), "groups": _groups(), "upstream_description": gateway.description("upstream")}
    kept = [sid for sid in ("crud-one", "found-one") if sid in booted["ids"]]
    booted["policies"] = {sid: gateway.policies(sid) for sid in kept}
    result = gateway.reload()
    return {
        "booted": booted,
        "removed_by_a_reload": result["mcp_servers_removed"],
        "ids_after_reload": gateway.ids(),
        "policies_after_reload": {sid: gateway.policies(sid) for sid in kept},
        "rescan": gateway.scan(),
    }


def _takeover(gateway: _Operator, config: Path, backend: str, workdir: Path) -> dict[str, Any]:
    process = gateway.start("crud-one")
    _write_definition(workdir)
    _write(config, _config(backend, FILES["taken_over"]))
    result = gateway.reload()
    described = {sid: gateway.description(sid) for sid in TAKEN_OVER}
    out = {
        "updated": result["mcp_servers_updated"],
        "removed": result["mcp_servers_removed"],
        "added": result["mcp_servers_added"],
        "crud_process_exited": _exited(process),
        "described": described,
        "provenance": {sid: gateway.repository.get(sid).provenance.value for sid in TAKEN_OVER},
        "policies": {sid: gateway.policies(sid) for sid in TAKEN_OVER},
        "rescan": gateway.scan(),
    }
    # The source reports a changed definition: the file still wins.
    _write_definition(workdir, "a changed definition")
    changed = gateway.scan()
    pending = changed.pop("pending")
    out["changed_definition"] = {
        **changed,
        "pending": pending,
        "described": {sid: gateway.description(sid) for sid in TAKEN_OVER},
    }
    out["approve"] = gateway.send("POST", "/discovery/approve/found-one", {}).json()
    out["described_after_approve"] = gateway.description("found-one")
    _write_definition(workdir)
    out["delete_status"] = gateway.send("DELETE", "/mcp_servers/crud-one").status_code
    return out


def _dropped(gateway: _Operator, config: Path, backend: str) -> dict[str, Any]:
    _write(config, _config(backend, FILES["dropped"]))
    result = gateway.reload()
    return {"removed": result["mcp_servers_removed"], "ids": gateway.ids()}


def _boot_only(gateway: _Operator) -> dict[str, Any]:
    return {"ids": gateway.ids(), "described": {sid: gateway.description(sid) for sid in TAKEN_OVER}}


#: The file each phase boots from, unless the phase before left one.
BOOT_FILE = {"first": "boot"}


def run(phase: str, backend: str, workdir: Path) -> dict[str, Any]:
    from starlette.testclient import TestClient

    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import ServerLifecycle

    config = workdir / "config.yaml"
    if phase in BOOT_FILE:
        _write_definition(workdir)
        _write(config, _config(backend, FILES[BOOT_FILE[phase]], members=("member", "gone-member")))
    context = bootstrap(config_path=str(config))
    repository = context.runtime.repository
    key = context.auth_components.api_key_store.create_key(principal_id=PRINCIPAL, name="operator")
    app = _served_app(context, ServerLifecycle(context))
    with TestClient(app, base_url=BASE_URL) as client:
        gateway = _Operator(client, key, repository)
        if phase == "first":
            out = _first(gateway, config, backend)
        elif phase == "restart":
            out = _restart(gateway)
        elif phase == "takeover":
            out = _takeover(gateway, config, backend, workdir)
        elif phase == "dropped":
            out = _dropped(gateway, config, backend)
        else:
            out = _boot_only(gateway)
    for server in repository.get_all().values():
        server.shutdown()
    return out


def main(phase: str, backend: str, workdir: Path, out: Path) -> None:
    os.chdir(workdir)  # bootstrap keeps its data under ./data
    out.write_text(json.dumps(run(phase, backend, workdir)))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]))
