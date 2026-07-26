"""Answer Hangar's mid-flight consent prompt (human-in-the-loop) and assert the outcome.

The interactive half of ADR-014. When the upstream parks a task in
``input_required``, Hangar elicits the downstream client -- ``elicitation/create``,
sent in-handler during the ``tasks/get`` that observes the pause -- and opens the
consent gate only on ``action == "accept"``.

    python examples/task_upstream/consent_hitl.py --decision accept   # -> completed
    python examples/task_upstream/consent_hitl.py --decision decline  # -> failed

Both are passing runs: the point is that the gate follows the human, and that a
decline fails the task closed rather than letting it through. A client that never
negotiates the ``elicitation`` capability is a third case -- Hangar cannot ask,
so it fails closed; ``drive_relay.py`` covers that one.

Note this is NOT the synchronous L7 ``requireApproval`` mechanism, which is
non-interactive and fail-closed by policy.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

import anyio

from _session import Checks, open_session, task_id_from_hangar_call

TERMINAL = ("completed", "failed", "cancelled")


async def run(url: str, server: str, decision: str) -> int:
    checks = Checks()

    async def answer_prompt(method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Reply to whatever Hangar asks. The gate reads ``action``."""
        message = (params.get("message") or "")[:100]
        print(f"  <- {method}: {message}\n  -> action={decision}")
        return {"action": decision, "content": {}}

    async with open_session(
        url,
        client_name="consent-hitl",
        # Without this capability Hangar has nobody to ask and fails the gate closed.
        capabilities={"elicitation": {}},
        on_inbound_request=answer_prompt,
    ) as session:
        batch = await session.request(
            "tools/call",
            {
                "name": "hangar_call",
                "arguments": {
                    "calls": [{"mcp_server": server, "tool": "long_job_consent", "arguments": {"prompt": "gate"}}]
                },
            },
        )
        task_id, first_result = task_id_from_hangar_call(batch)
        if not checks.check("the consent tool returns a relayed task handle", bool(task_id), str(first_result)[:200]):
            return checks.summary()

        status = None
        for _ in range(30):
            status = (await session.request("tasks/get", {"taskId": task_id})).get("status")
            if status in TERMINAL:
                break
            await anyio.sleep(0.5)

        expected = "completed" if decision == "accept" else "failed"
        checks.check(
            "Hangar prompted the client for consent",
            bool(session.inbound_methods),
            str(session.inbound_methods),
        )
        checks.check(f"the {decision} decision resolves the task to {expected}", status == expected, f"status={status}")

    return checks.summary()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8080/mcp", help="Hangar's MCP endpoint")
    parser.add_argument("--server", default="task-upstream", help="the upstream's id as registered in Hangar")
    parser.add_argument("--decision", choices=("accept", "decline"), default="accept", help="what the human answers")
    args = parser.parse_args()
    print(f"== HITL consent ({args.decision}) against {args.url} ==")
    sys.exit(anyio.run(run, args.url, args.server, args.decision))


if __name__ == "__main__":
    main()
