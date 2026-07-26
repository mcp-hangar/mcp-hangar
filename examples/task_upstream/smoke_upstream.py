"""Smoke the upstream DIRECTLY, asserting the wire shapes Hangar's relay expects.

No Hangar involved: this is the contract check for the upstream itself. Run it
first when something breaks, to tell "the upstream regressed" apart from "the
relay regressed".

    python examples/task_upstream/smoke_upstream.py --url http://127.0.0.1:8080/mcp

Exits non-zero if any assertion fails.
"""

from __future__ import annotations

import argparse
import sys

import anyio

from _session import Checks, JsonRpcError, open_session

TASK_TOOLS = ("long_job", "long_job_consent")


async def run(url: str) -> int:
    checks = Checks()
    async with open_session(url, client_name="upstream-smoke") as session:
        print(f"initialize protocolVersion: {session.initialize_result.get('protocolVersion')}")

        tools = await session.request("tools/list")
        names = {tool["name"] for tool in tools["tools"]}
        checks.check("tools/list advertises the task tools", set(TASK_TOOLS) <= names, f"names={sorted(names)}")

        # A plain tool still returns an inline result -- adding the Tasks surface
        # must not disturb ordinary calls.
        echoed = await session.request("tools/call", {"name": "echo", "arguments": {"text": "ping"}})
        checks.check(
            "a non-task tool still returns inline content",
            (echoed.get("content") or [{}])[0].get("text") == "ping",
            str(echoed)[:120],
        )

        # 1. tools/call returns a task handle, not content.
        created = await session.request("tools/call", {"name": "long_job", "arguments": {"prompt": "hello"}})
        task = created.get("task") or {}
        task_id = task.get("taskId")
        checks.check("tools/call returns a task handle", bool(task_id), str(created)[:160])
        checks.check("the fresh task is working", task.get("status") == "working", f"status={task.get('status')}")
        if not task_id:
            return checks.summary()

        # 2. tasks/get accepts snake_case (what the relay forwards) and reaches completed.
        immediate = await session.request("tasks/get", {"task_id": task_id})
        checks.check(
            "tasks/get accepts snake_case task_id (relay spelling)",
            immediate.get("status") in ("working", "completed"),
            f"status={immediate.get('status')}",
        )

        status = None
        for _ in range(20):
            status = (await session.request("tasks/get", {"task_id": task_id})).get("status")
            if status in ("completed", "failed"):
                break
            await anyio.sleep(0.5)
        checks.check("tasks/get reaches completed", status == "completed", f"status={status}")

        # 3. tasks/result carries the payload.
        payload = await session.request("tasks/result", {"task_id": task_id})
        text = (payload.get("content") or [{}])[0].get("text", "")
        checks.check("tasks/result returns the tool payload", text.startswith("Completed job"), text[:80])

        # 4. tasks/list includes it.
        listed = await session.request("tasks/list")
        ids = [entry.get("taskId") for entry in listed.get("tasks") or []]
        checks.check("tasks/list includes the task", task_id in ids, f"count={len(ids)}")

        # 5. Unknown task fails closed rather than inventing a task.
        try:
            await session.request("tasks/get", {"task_id": "does-not-exist-000"})
            checks.check("unknown task_id fails closed", False, "unexpected success")
        except JsonRpcError as error:
            checks.check("unknown task_id fails closed", "not found" in error.message.lower(), error.message[:80])

        # 6. Consent branch: input_required, then tasks/update resolves it.
        consent = await session.request("tools/call", {"name": "long_job_consent", "arguments": {"prompt": "gate"}})
        consent_id = (consent.get("task") or {}).get("taskId")
        consent_status = None
        for _ in range(20):
            consent_status = (await session.request("tasks/get", {"task_id": consent_id})).get("status")
            if consent_status != "working":
                break
            await anyio.sleep(0.5)
        checks.check(
            "the consent tool parks in input_required",
            consent_status == "input_required",
            f"status={consent_status}",
        )
        if consent_status == "input_required":
            updated = await session.request("tasks/update", {"task_id": consent_id, "input_key": "x"})
            checks.check(
                "tasks/update resolves input_required to completed",
                updated.get("status") == "completed",
                f"status={updated.get('status')}",
            )

        # 7. Cancel a fresh working task.
        cancellable = await session.request("tools/call", {"name": "long_job", "arguments": {"prompt": "cancelme"}})
        cancelled = await session.request("tasks/cancel", {"task_id": (cancellable["task"])["taskId"]})
        checks.check(
            "tasks/cancel confirms cancelled",
            cancelled.get("status") == "cancelled",
            f"status={cancelled.get('status')}",
        )

    return checks.summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080/mcp", help="the upstream's MCP endpoint")
    args = parser.parse_args()
    print(f"== upstream smoke against {args.url} ==")
    sys.exit(anyio.run(run, args.url))


if __name__ == "__main__":
    main()
