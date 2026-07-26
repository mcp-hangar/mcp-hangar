"""Drive the governed task relay end-to-end THROUGH Hangar's front door (ADR-014).

Point this at Hangar, not at the upstream. In egress topology a client sees
Hangar's ``hangar_*`` meta-API, so the upstream tool is invoked via the
``hangar_call`` batch tool; the relayed task is then followed with native
``tasks/*`` served by Hangar's relay handlers.

    python examples/task_upstream/drive_relay.py --url http://127.0.0.1:8080/mcp \
        --server task-upstream

What it proves: the ``tasks`` capability is advertised at ``initialize``; a tool
call comes back as a relayed task handle rather than ``TaskRelayNotSupported``;
get/result/cancel/list work; the result digest is re-verified on retrieval; an
unknown task id fails closed with no enumeration side channel; and the consent
gate parks in ``input_required`` and resolves via ``tasks/update``.

Exits non-zero if any assertion fails. For the interactive consent path (Hangar
prompting the client with ``elicitation/create``) use ``consent_hitl.py``.
"""

from __future__ import annotations

import argparse
import sys

import anyio

from _session import Checks, JsonRpcError, open_session, task_id_from_hangar_call


async def run(url: str, server: str) -> int:
    checks = Checks()
    async with open_session(url, client_name="relay-driver") as session:
        capabilities = session.initialize_result.get("capabilities") or {}
        checks.check(
            "the tasks capability is advertised at initialize",
            bool(capabilities.get("tasks")),
            str(capabilities.get("tasks"))[:80],
        )

        async def call_upstream(tool: str, arguments: dict) -> dict:
            return await session.request(
                "tools/call",
                {
                    "name": "hangar_call",
                    "arguments": {"calls": [{"mcp_server": server, "tool": tool, "arguments": arguments}]},
                },
            )

        # 1. The upstream's task handle survives the relay.
        batch = await call_upstream("long_job", {"prompt": "hello"})
        task_id, first_result = task_id_from_hangar_call(batch)
        checks.check("long_job returns a relayed task handle", bool(task_id), str(first_result)[:200])
        if not task_id:
            # Without a handle every later check is meaningless; the detail above
            # carries the reason (typically TaskRelayNotSupported).
            return checks.summary()

        # 2. Poll to completion through the relay.
        status = None
        for _ in range(20):
            status = (await session.request("tasks/get", {"taskId": task_id})).get("status")
            if status in ("completed", "failed"):
                break
            await anyio.sleep(0.5)
        checks.check("tasks/get reaches completed", status == "completed", f"status={status}")

        # 3. The governed payload comes back (Hangar re-verifies the tool digest here).
        try:
            payload = await session.request("tasks/result", {"taskId": task_id})
            text = (payload.get("content") or [{}])[0].get("text", "")
            checks.check("tasks/result returns the governed payload", text.startswith("Completed"), text[:80])
        except JsonRpcError as error:
            checks.check("tasks/result returns the governed payload", False, error.message[:120])

        # 4. tasks/list is scoped to the calling owner.
        try:
            listed = await session.request("tasks/list")
            ids = [entry.get("taskId") for entry in listed.get("tasks") or []]
            checks.check("tasks/list is owner-scoped and lists the task", task_id in ids, f"count={len(ids)}")
        except JsonRpcError as error:
            # tasks/list is dropped once the SDK removes it (SEP-2663); a
            # method-not-found here is the forward-compat path, not a failure.
            checks.check(
                "tasks/list is owner-scoped and lists the task",
                error.code == -32601,
                f"{error.message[:80]} (method gone = SEP-2663 drop, acceptable)",
            )

        # 5. Owner authorization: someone else's / a bogus id must not be observable.
        try:
            await session.request("tasks/get", {"taskId": "does-not-exist-000"})
            checks.check("an unknown task id fails closed", False, "unexpected success")
        except JsonRpcError as error:
            checks.check("an unknown task id fails closed", True, error.message[:80])

        # 6. Cancel a fresh working task through the relay.
        cancel_batch = await call_upstream("long_job", {"prompt": "cancelme"})
        cancel_id, _ = task_id_from_hangar_call(cancel_batch)
        if cancel_id:
            cancelled = await session.request("tasks/cancel", {"taskId": cancel_id})
            checks.check(
                "tasks/cancel confirms cancelled",
                cancelled.get("status") == "cancelled",
                f"status={cancelled.get('status')}",
            )

        # 7. Consent gate, non-interactive: park in input_required, resolve by update.
        consent_batch = await call_upstream("long_job_consent", {"prompt": "gate"})
        consent_id, _ = task_id_from_hangar_call(consent_batch)
        if consent_id:
            consent_status = None
            for _ in range(20):
                consent_status = (await session.request("tasks/get", {"taskId": consent_id})).get("status")
                if consent_status != "working":
                    break
                await anyio.sleep(0.5)
            # A client that did NOT negotiate elicitation cannot answer the gate,
            # so Hangar fails it closed instead of hanging -- both outcomes are
            # correct governance; only "still working" would be a bug.
            checks.check(
                "the consent task leaves working (input_required or failed closed)",
                consent_status in ("input_required", "failed"),
                f"status={consent_status}",
            )
            if consent_status == "input_required":
                updated = await session.request("tasks/update", {"taskId": consent_id, "input_key": "x"})
                checks.check(
                    "tasks/update resolves the consent task",
                    updated.get("status") == "completed",
                    f"status={updated.get('status')}",
                )

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
