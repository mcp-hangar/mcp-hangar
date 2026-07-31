"""Drive the governed task relay end-to-end THROUGH Hangar's front door.

Point this at Hangar, not at the upstream. In egress topology a client sees
Hangar's ``hangar_*`` meta-API, so the upstream tool is invoked via the
``hangar_call`` batch tool; the relayed task is then followed with the SEP-2663
``tasks/*`` methods Hangar's relay handlers serve.

    python examples/task_upstream/drive_relay.py --url http://127.0.0.1:8080/mcp \
        --server task-upstream

The example upstream is deliberately on the OLDER design -- it answers
``tasks/get`` with a status and keeps the payload behind ``tasks/result``. Hangar
serves SEP-2663 downstream and bridges the difference, so a green run here proves
the BRIDGE works, not that the upstream is modern. That bridge is exactly what
broke once without anyone noticing, because these drivers are not in CI.

The relay is live by default (``relay_tasks_enabled``); ``config.yaml`` spells
it out anyway so the example still runs against a deployment that turned it off.

Exits non-zero if any assertion fails.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import anyio
from mcp.shared.exceptions import MCPError

from _session import (
    TASKS_EXTENSION,
    Checks,
    call_removed,
    cancel_task,
    discover,
    get_task,
    open_client,
    task_id_from_hangar_call,
    update_task,
)

_POLL_ATTEMPTS = 40
_POLL_DELAY = 0.25


async def _poll_until(session: Any, task_id: str, predicate: Any) -> dict[str, Any]:
    """Poll ``tasks/get`` until *predicate* holds or the attempts run out."""
    snapshot: dict[str, Any] = {}
    for _ in range(_POLL_ATTEMPTS):
        snapshot = await get_task(session, task_id)
        if predicate(snapshot):
            return snapshot
        await anyio.sleep(_POLL_DELAY)
    return snapshot


async def _check_advertisement(session: Any, checks: Checks) -> None:
    """Capabilities come from ``server/discover``, not from a handshake.

    A 2026-07-28 connection has no ``initialize`` to learn them from -- that is
    the point of SEP-2575 -- and the entry must sit under ``extensions``, since
    the 2026-07-28 ``ServerCapabilities`` has no ``tasks`` field and a server
    advertising it there has it sieved out of this very response.
    """
    advertised = (await discover(session)).get("capabilities") or {}
    extensions = advertised.get("extensions") or {}
    tasks_extension = extensions.get(TASKS_EXTENSION)

    checks.check(
        "the tasks extension is advertised on server/discover",
        tasks_extension is not None,
        f"capabilities={sorted(advertised)} extensions={sorted(extensions)}",
    )
    checks.check(
        "the legacy capabilities.tasks field is not used",
        advertised.get("tasks") is None,
        str(advertised.get("tasks")),
    )
    advertised_methods = (tasks_extension or {}).get("methods") or []
    checks.check(
        "the advertisement does not claim the removed methods",
        "tasks/list" not in advertised_methods and "tasks/result" not in advertised_methods,
        str(advertised_methods),
    )


async def _check_wire_shape(snapshot: dict[str, Any], checks: Checks) -> None:
    """The wire is SEP-2663, not the SEP-1686 fossil `mcp_types` still carries."""
    checks.check("the snapshot is flat", "task" not in snapshot, str(sorted(snapshot))[:120])
    checks.check("the snapshot carries resultType", snapshot.get("resultType") == "complete")
    checks.check("TTL is ttlMs, not the fossil's ttl", "ttl" not in snapshot)
    checks.check("the poll hint is pollIntervalMs, not pollInterval", "pollInterval" not in snapshot)


async def _check_removed_methods(session: Any, task_id: str, checks: Checks) -> None:
    for removed in ("tasks/result", "tasks/list"):
        try:
            await call_removed(session, removed, task_id)
            checks.check(f"{removed} is removed", False, "the server answered it")
        except MCPError as error:
            checks.check(f"{removed} is removed", error.code == -32601, f"code={error.code}")


async def _check_consent(session: Any, call_upstream: Any, checks: Checks) -> None:
    """A paused task states what it needs; the client answers via tasks/update."""
    batch = await call_upstream("long_job_consent", {"prompt": "who are you"})
    consent_id, first = task_id_from_hangar_call(batch)
    if not checks.check("long_job_consent returns a relayed task handle", bool(consent_id), str(first)[:200]):
        return

    paused = await _poll_until(session, consent_id, lambda s: s.get("status") == "input_required")
    checks.check("the task parks in input_required", paused.get("status") == "input_required", str(paused)[:120])

    # `inputRequests` is what a client keys its answers on. It reaches here only
    # if the upstream emits it AND Hangar carries it through -- python-sdk#3005's
    # own GetTaskResult drops the map on parse, which is why the vendored model
    # declares it explicitly.
    requests = paused.get("inputRequests") or {}
    checks.check("the paused snapshot carries inputRequests", bool(requests), str(requests)[:160])

    answers = {key: {"content": {"answer": "ada"}} for key in requests} or {"consent": {"content": {}}}
    try:
        ack = await update_task(session, consent_id, answers)
        checks.check("tasks/update acknowledges empty", "status" not in ack, str(ack)[:120])
    except MCPError as error:
        checks.check("tasks/update acknowledges empty", False, f"[{error.code}] {error.message}"[:160])
        return

    resolved = await _poll_until(session, consent_id, lambda s: s.get("status") != "input_required")
    checks.check(
        "the task leaves input_required after the update",
        resolved.get("status") != "input_required",
        str(resolved)[:120],
    )


async def _drive_happy_path(client: Any, server: str, checks: Checks) -> None:
    session = client.session
    await _check_advertisement(session, checks)

    async def call_upstream(tool: str, arguments: dict[str, Any]) -> Any:
        return await client.call_tool(
            "hangar_call",
            {"calls": [{"mcp_server": server, "tool": tool, "arguments": arguments}]},
        )

    batch = await call_upstream("long_job", {"prompt": "hello"})
    task_id, first_result = task_id_from_hangar_call(batch)
    if not checks.check("long_job returns a relayed task handle", bool(task_id), str(first_result)[:200]):
        # Without a handle every later check is meaningless; the detail above
        # carries the reason (typically the kill-switch being off).
        return

    snapshot = await _poll_until(session, task_id, lambda s: s.get("status") == "completed")
    checks.check("tasks/get reaches completed", snapshot.get("status") == "completed", str(snapshot)[:120])
    # SEP-2663 folds the removed tasks/result round trip into the poll. This
    # upstream keeps its payload behind that method, so a pass here means Hangar
    # fetched it on the client's behalf.
    checks.check(
        "the outcome arrives inline on the poll",
        isinstance(snapshot.get("result"), dict),
        str(snapshot.get("result"))[:120],
    )

    await _check_wire_shape(snapshot, checks)
    await _check_removed_methods(session, task_id, checks)

    try:
        await get_task(session, "does-not-exist-000")
        checks.check("an unknown task id is refused", False, "the server answered it")
    except MCPError as error:
        checks.check("an unknown task id is refused", error.code == -32602, f"code={error.code}")

    # Cancellation is cooperative, so the ack must not claim an outcome.
    cancel_batch = await call_upstream("long_job", {"prompt": "to-cancel"})
    cancel_id, _ = task_id_from_hangar_call(cancel_batch)
    if cancel_id:
        ack = await cancel_task(session, cancel_id)
        checks.check("tasks/cancel acknowledges without a status", "status" not in ack, str(ack)[:120])
        checks.check("the cancel ack still carries resultType", ack.get("resultType") == "complete")

    await _check_consent(session, call_upstream, checks)


async def _drive_undeclared_client(url: str, checks: Checks) -> None:
    """A modern client that never declared the extension gets an ACTIONABLE refusal.

    Unlike a legacy connection -- told the methods do not exist -- this one can
    fix its declaration and retry, so the error names what to add.
    """
    async with open_client(url, client_name="undeclared-driver", declare_tasks=False) as bare:
        try:
            await get_task(bare.session, "any-task-id")
            checks.check("an undeclared client is refused -32021", False, "the server answered it")
        except MCPError as error:
            checks.check("an undeclared client is refused -32021", error.code == -32021, f"code={error.code}")
            required = error.data.get("requiredCapabilities") if isinstance(error.data, dict) else None
            checks.check(
                "the refusal names the extension to declare",
                bool(required) and TASKS_EXTENSION in str(required),
                str(required)[:140],
            )


async def run(url: str, server: str) -> int:
    checks = Checks()
    async with open_client(url, client_name="relay-driver") as client:
        await _drive_happy_path(client, server, checks)
    await _drive_undeclared_client(url, checks)
    return checks.summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080/mcp", help="Hangar's MCP endpoint")
    parser.add_argument("--server", default="task-upstream", help="the upstream's id as registered in Hangar")
    args = parser.parse_args()
    print(f"== relay drive against {args.url} (upstream: {args.server}) ==")
    sys.exit(anyio.run(run, args.url, args.server))


if __name__ == "__main__":
    main()
