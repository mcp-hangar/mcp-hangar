"""Each caller's own command-bus rate limit, on the app ``serve --http`` serves (#1471).

Two tenants, each with its own API key, call over the real streamable-HTTP
transport (``_front_door_harness``), served in ``egress``: a tool call is a
``hangar_call``, whose ``InvokeToolCommand`` goes through the command bus. The
rate limit is put in force from a ``rate_limit`` section by the function
startup uses. Nothing on the call path is patched: the caller a call is charged
to is the one its API key authenticated, carried into the executor's worker
thread.

* One caller using up its own budget does not refuse the other.
* ``hangar_group_list`` answers while the caller's budget and the shared one
  are used up.
* The shared budget still applies, and calls a caller's own budget refused
  spent none of it.
* Without ``per_caller``, the shared budget refuses as it did before.

Naming: neutral placeholders only (store, read_item, tenant:a, tenant:b).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from typing import Any

from mcp_hangar.bootstrap.runtime import install_command_bus_rate_limit
from mcp_hangar.domain.security.rate_limiter import reset_rate_limiter
from mcp_hangar.infrastructure.caller_rate_limit import reset_caller_rate_limit
from mcp_hangar.server.context import get_context
from tests.integration._front_door_harness import FrontDoor, front_door, jsonrpc, SERVER, TENANT_A, TENANT_B

READ = "read_item"

#: Tokens per second: nothing refills while a test runs.
NEVER = 0.001

OWN = "this caller's rate limit"
SHARED = "the rate limit all callers share"


@contextmanager
def _served(rate_limit: dict[str, Any]) -> Iterator[FrontDoor]:
    """A gateway served in ``egress``, with *rate_limit* in force as startup puts it."""
    reset_rate_limiter()
    try:
        with front_door((READ,), topology="egress") as door:
            install_command_bus_rate_limit(get_context().runtime, {"rate_limit": rate_limit}, env={})
            yield door
    finally:
        reset_caller_rate_limit()
        reset_rate_limiter()


def _hangar_call(door: FrontDoor, tenant: str) -> dict[str, Any]:
    """The one call result of a ``hangar_call`` of the read tool."""
    calls = [{"mcp_server": SERVER, "tool": READ, "arguments": {}}]
    payload = jsonrpc(door.call(tenant, "hangar_call", {"calls": calls}))
    assert "result" in payload, payload
    (call,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return dict(call)


def _ran(calls: list[dict[str, Any]]) -> list[bool]:
    return [call["success"] for call in calls]


def _refused_by(call: dict[str, Any], budget: str) -> None:
    assert (call["success"], call["error_type"]) == (False, "RateLimitExceeded"), call
    assert call["error"].startswith(f"RateLimitExceeded: {budget} for InvokeToolCommand is used up"), call
    assert "Retry after" in call["error"], call


def test_one_caller_using_up_its_budget_does_not_refuse_another():
    with _served({"rps": 1000, "burst": 1000, "per_caller": {"rps": NEVER, "burst": 2}}) as door:
        first = [_hangar_call(door, TENANT_A) for _ in range(3)]
        second = [_hangar_call(door, TENANT_B) for _ in range(2)]
        reached = door.upstream.called.count(READ)

    assert _ran(first) == [True, True, False]
    _refused_by(first[2], OWN)
    assert _ran(second) == [True, True]
    assert reached == 4


def test_the_group_listing_answers_while_the_budgets_are_used_up():
    with _served({"rps": NEVER, "burst": 1, "per_caller": {"rps": NEVER, "burst": 1}}) as door:
        served = _hangar_call(door, TENANT_A)
        refused = _hangar_call(door, TENANT_A)
        listings = [jsonrpc(door.call(TENANT_A, "hangar_group_list")) for _ in range(3)]

    assert served["success"] is True
    _refused_by(refused, OWN)
    for listing in listings:
        assert "result" in listing and not listing["result"].get("isError"), listing
        assert "groups" in listing["result"]["content"][0]["text"], listing


def test_the_shared_budget_still_applies():
    with _served({"rps": NEVER, "burst": 3, "per_caller": {"rps": NEVER, "burst": 2}}) as door:
        first = [_hangar_call(door, TENANT_A) for _ in range(4)]
        second = [_hangar_call(door, TENANT_B) for _ in range(2)]

    assert _ran(first) == [True, True, False, False]
    _refused_by(first[2], OWN)
    _refused_by(first[3], OWN)
    # The shared budget had one token left: A's refused calls spent none of it.
    assert _ran(second) == [True, False]
    _refused_by(second[1], SHARED)


def test_without_per_caller_the_shared_budget_refuses_as_before():
    with _served({"rps": NEVER, "burst": 2}) as door:
        calls = [_hangar_call(door, TENANT_A), _hangar_call(door, TENANT_B), _hangar_call(door, TENANT_A)]

    assert _ran(calls) == [True, True, False]
    _refused_by(calls[2], SHARED)
