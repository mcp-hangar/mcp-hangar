"""Create tasks through the served app, reload a file that takes their tools away, then follow the tasks up.

Run as a script, in its own interpreter, by
``test_a_task_follow_up_is_checked_against_current_tool_access.py``:
``python _task_follow_up_access_harness.py <topology> <out.json>``. Not
collected by pytest.

What runs is production, as in ``_front_door_task_governance_harness.py``,
whose upstreams, requests and keys this reuses: ``bootstrap()`` reading a
config file, API-key auth, the governed task relay, the app ``serve --http``
serves under starlette's ``TestClient``, and two in-process HTTP MCP upstreams
whose every ``tools/call`` answers with a task.

1. Each tenant calls every tool. Nothing is withdrawn or denied yet, so each
   call answers with a task.
2. The config file is rewritten and reloaded through the reload command
   ``POST /api/config/reload`` and ``hangar_reload_config`` send. The new file
   withdraws a group tool for every tenant and another for ``tenant-a``, denies
   a group tool, and denies a tool on the ungrouped server for ``tenant-a``.
3. For each task, its owner sends ``tasks/get``, then ``tasks/update``, then
   ``tasks/cancel``, and then calls the task's tool again.

The report holds, for each tenant and tool, what each of those answered and
whether the upstream was sent the update and the cancel.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from _front_door_task_governance_harness import (
    BASE_URL,
    FRONT_DOOR,
    TENANTS,
    _flat_call,
    _hangar_call,
    _keys,
    _poll,
    _post,
    _upstream,
)

GROUP = "job-pool"
MEMBER = "job-a"
SOLO = "job-solo"
GROUP_TOOLS = ("job", "job_withdrawn", "job_withdrawn_a", "job_denied")
SOLO_TOOLS = ("solo_job", "solo_denied_a")


def _config(topology: str, group_endpoint: str, solo_endpoint: str, *, taken_away: bool) -> dict[str, Any]:
    group: dict[str, Any] = {
        "mode": "group",
        "strategy": "priority",
        "min_healthy": 1,
        "members": [{"id": MEMBER, "priority": 1}],
    }
    solo: dict[str, Any] = {"mode": "remote", "endpoint": solo_endpoint}
    if taken_away:
        group["tools"] = {"deny_list": ["job_denied"]}
        group["tool_projection"] = {
            "withdrawn": ["job_withdrawn"],
            "tenant_overrides": {TENANTS[0]: {"withdrawn": ["job_withdrawn_a"]}},
        }
        solo["tool_access"] = {"member": {TENANTS[0]: {"deny_list": ["solo_denied_a"]}}}
    return {
        "tool_access": {"mode": topology},
        "rate_limit": {"rps": 1000, "burst": 1000},
        "config_reload": {"enabled": False},
        "auth": {
            "enabled": True,
            "allow_anonymous": False,
            "api_key": {"enabled": True, "header_name": "X-API-Key"},
            "storage": {"driver": "memory"},
        },
        "mcp_servers": {MEMBER: {"mode": "remote", "endpoint": group_endpoint}, SOLO: solo, GROUP: group},
    }


def _answer(payload: dict[str, Any]) -> dict[str, Any]:
    """A follow-up's answer: served, or refused with its code, message and ``data.error_type``."""
    if "error" not in payload:
        return {"outcome": "ok"}
    error = payload["error"]
    return {
        "outcome": "refused",
        "code": error.get("code"),
        "message": error.get("message"),
        "error_type": (error.get("data") or {}).get("error_type"),
    }


def main(topology: str, out: Path) -> None:
    os.chdir(out.parent)  # bootstrap keeps its data under ./data
    group_endpoint, group_upstream = _upstream(GROUP_TOOLS)
    solo_endpoint, solo_upstream = _upstream(SOLO_TOOLS)

    from starlette.testclient import TestClient

    import mcp_hangar
    from mcp_hangar.application.commands.commands import ReloadConfigurationCommand
    from mcp_hangar.server.api.middleware import create_auth_enforced_app
    from mcp_hangar.server.bootstrap import bootstrap
    from mcp_hangar.server.lifecycle import mcp_app_for_serving, warm_the_front_door_catalogue
    from mcp_hangar.tasks_wire import EXTENSION_ID

    config_file = out.parent / "config.yaml"
    config_file.write_text(json.dumps(_config(topology, group_endpoint, solo_endpoint, taken_away=False)))
    context = bootstrap(config_path=str(config_file))
    warm_the_front_door_catalogue(context.runtime)
    keys = _keys(context)
    app = create_auth_enforced_app(mcp_app_for_serving(context.mcp_server), context.auth_components)
    tasks_capability = {"extensions": {EXTENSION_ID: {}}}

    def call(client: Any, tenant: str, tool: str) -> dict[str, Any]:
        if topology == FRONT_DOOR:
            return _flat_call(client, keys[tenant], tool)
        return _hangar_call(client, keys[tenant], GROUP if tool in GROUP_TOOLS else SOLO, tool)

    def follow_up(client: Any, tenant: str, method: str, task_id: str, params: dict[str, Any]) -> dict[str, Any]:
        payload = _post(
            client, keys[tenant], method, {"taskId": task_id, **params}, name=task_id, capabilities=tasks_capability
        )
        return _answer(payload)

    def sent(method: str, task_id: str) -> bool:
        return (method, task_id) in [*group_upstream.follow_ups, *solo_upstream.follow_ups]

    report: dict[str, dict[str, dict[str, Any]]] = {}
    with TestClient(app, base_url=BASE_URL) as client:
        created = {
            tenant: {tool: call(client, tenant, tool) for tool in (*GROUP_TOOLS, *SOLO_TOOLS)} for tenant in TENANTS
        }

        config_file.write_text(json.dumps(_config(topology, group_endpoint, solo_endpoint, taken_away=True)))
        reloaded = context.runtime.command_bus.send(
            ReloadConfigurationCommand(config_path=str(config_file), requested_by="harness")
        )
        # One call to each server, so every follow-up below reaches a running one.
        for tool in (GROUP_TOOLS[0], SOLO_TOOLS[0]):
            call(client, TENANTS[1], tool)

        for tenant in TENANTS:
            rows: dict[str, dict[str, Any]] = {}
            for tool, made in created[tenant].items():
                row: dict[str, Any] = {"created": made["outcome"]}
                task_id = made.get("task_id")
                if task_id:
                    row["polled"] = _poll(client, keys[tenant], task_id, tasks_capability)
                    row["update"] = follow_up(
                        client, tenant, "tasks/update", task_id, {"inputResponses": {"answer": {"content": {}}}}
                    )
                    row["update_sent"] = sent("tasks/update", task_id)
                    row["cancel"] = follow_up(client, tenant, "tasks/cancel", task_id, {})
                    row["cancel_sent"] = sent("tasks/cancel", task_id)
                row["called_again"] = call(client, tenant, tool)
                rows[tool] = row
            report[tenant] = rows

    for server in context.runtime.repository.get_all().values():
        server.shutdown()
    out.write_text(json.dumps({"hangar": mcp_hangar.__file__, "reload": reloaded, "tasks": report}, default=str))
    sys.stdout.flush()
    sys.stderr.flush()
    # The worker threads are daemons mid-sleep; nothing to wait for.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]))
