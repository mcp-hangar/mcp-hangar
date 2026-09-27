"""A stdio MCP backend whose ``job`` tool creates a task, for the T3 task follow-up check (#1615).

Raw JSON-RPC, one message per line, because an SDK server does not answer a
plain ``tools/call`` with a task handle. Every ``job`` call mints a new task and
answers ``{"task": {...}}``, the nested shape Hangar relays to its caller. It
then answers the follow-ups Hangar relays for that task:

``tasks/get``, ``tasks/update`` and ``tasks/cancel`` each read the id from
``taskId``, its wire name in SEP-2663, and from nothing else (#1617).

A follow-up for an id it did not mint, or under another name, is an error, so a
change in what Hangar relays fails the test instead of passing unnoticed.

A task id is ``task-`` plus a random hex string: nothing else in an exported
span can contain it by accident, so finding it there means it leaked.

Run as ``python _task_server.py <log>``. Each request it answers is appended to
``<log>`` as one JSON line: its method, its param names and the id it read.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any

#: The follow-ups it answers. Each carries the task id as ``taskId``, its wire name.
_FOLLOW_UPS = frozenset({"tasks/get", "tasks/cancel", "tasks/update"})
_TOOL = "job"


def _task(task_id: str, status: str) -> dict[str, Any]:
    return {
        "taskId": task_id,
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }


def _answer(method: str, params: dict[str, Any], tasks: dict[str, str]) -> tuple[dict[str, Any], str | None]:
    """The response body for one request, and the task id it named."""
    if method == "initialize":
        info = {"name": "task-provider", "version": "0"}
        return {"result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": info}}, None
    if method == "tools/list":
        return {"result": {"tools": [{"name": _TOOL, "inputSchema": {"type": "object"}}]}}, None
    if method == "tools/call" and params.get("name") == _TOOL:
        task_id = f"task-{uuid.uuid4().hex}"
        tasks[task_id] = "working"
        return {"result": {"task": _task(task_id, "working")}}, task_id
    if method in _FOLLOW_UPS:
        task_id = params.get("taskId")
        if not isinstance(task_id, str) or task_id not in tasks:
            return {"error": {"code": -32602, "message": "unknown task"}}, None
        if method == "tasks/cancel":
            tasks[task_id] = "cancelled"
        return {"result": _task(task_id, tasks[task_id])}, task_id
    return {"error": {"code": -32601, "message": "method not found"}}, None


def main(log_file: str) -> None:
    tasks: dict[str, str] = {}
    for line in sys.stdin:
        if not line.strip():
            continue
        request = json.loads(line)
        if "id" not in request:  # a notification
            continue
        method = str(request.get("method"))
        params = request.get("params") or {}
        answer, task_id = _answer(method, params, tasks)
        with open(log_file, "a", encoding="utf-8") as log:
            log.write(json.dumps({"method": method, "params": sorted(params), "task_id": task_id}) + "\n")
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], **answer}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main(sys.argv[1])
