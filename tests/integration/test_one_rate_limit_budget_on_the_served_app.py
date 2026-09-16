"""One command-bus rate-limit budget per call path, on the app ``serve --http`` serves (#1481).

Two tenants, each with its own API key, call over the real streamable-HTTP
transport (``_front_door_harness``), served in ``egress``. The rate limit is put
in force from a ``rate_limit`` section by the function startup uses. Nothing on
the call path is patched.

* ``hangar_start`` is charged once, at the command bus, as the start it sends,
  whichever server it names: naming more servers buys no more starts.
* A bucket idle past the cleanup window grants only what it refilled.

Naming: neutral placeholders only (store, read_item, tenant:a, tenant:b).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import time
from typing import Any

from mcp_hangar.bootstrap.runtime import install_command_bus_rate_limit
from mcp_hangar.domain.security.rate_limiter import reset_rate_limiter
from mcp_hangar.infrastructure.caller_rate_limit import reset_caller_rate_limit
from mcp_hangar.server.context import get_context
from tests.integration._front_door_harness import FrontDoor, front_door, jsonrpc, SERVER, TENANT_A, TENANT_B

READ = "read_item"

#: Four more servers on the same upstream, each registered cold.
STORES = ("store-1", "store-2", "store-3", "store-4")

#: Tokens per second: nothing refills while a test runs.
NEVER = 0.001

OWN = "this caller's rate limit"
SHARED = "the rate limit all callers share"


@contextmanager
def _served(rate_limit: dict[str, Any]) -> Iterator[tuple[FrontDoor, Any]]:
    """A gateway served in ``egress`` with *rate_limit* in force as startup puts it, and its limiter."""
    reset_rate_limiter()
    try:
        with front_door((READ,), topology="egress", also=STORES) as door:
            runtime = get_context().runtime
            install_command_bus_rate_limit(runtime, {"rate_limit": rate_limit}, env={})
            yield door, runtime.rate_limiter
    finally:
        reset_caller_rate_limit()
        reset_rate_limiter()


def _start(door: FrontDoor, tenant: str, server: str) -> dict[str, Any]:
    """What a ``hangar_start`` of *server* answered."""
    payload = jsonrpc(door.call(tenant, "hangar_start", {"mcp_server": server}))
    assert "result" in payload, payload
    return dict(json.loads(payload["result"]["content"][0]["text"]))


def _started(answer: dict[str, Any]) -> bool:
    return answer.get("state") == "ready"


def _refused_by(answer: dict[str, Any], budget: str) -> None:
    assert answer.get("error_type") == "RateLimitExceeded", answer
    assert answer["error"].startswith(f"RateLimitExceeded: {budget} for StartMcpServerCommand is used up"), answer


def _hangar_call(door: FrontDoor, tenant: str) -> dict[str, Any]:
    """The one call result of a ``hangar_call`` of the read tool."""
    calls = [{"mcp_server": SERVER, "tool": READ, "arguments": {}}]
    payload = jsonrpc(door.call(tenant, "hangar_call", {"calls": calls}))
    assert "result" in payload, payload
    (call,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return dict(call)


def test_starting_many_servers_spends_one_caller_budget():
    with _served({"rps": 1000, "burst": 1000, "per_caller": {"rps": NEVER, "burst": 2}}) as (door, limiter):
        first = [_start(door, TENANT_A, server) for server in STORES]
        second = _start(door, TENANT_B, STORES[3])
        buckets = limiter.get_stats()["active_buckets"]

    assert [_started(answer) for answer in first] == [True, True, False, False]
    _refused_by(first[2], OWN)
    _refused_by(first[3], OWN)
    # The other caller's budget is its own.
    assert _started(second), second
    # One budget, the start's: none per server named, and none for the tool as well.
    assert buckets == 1


def test_starting_many_servers_spends_one_shared_budget():
    with _served({"rps": NEVER, "burst": 2}) as (door, limiter):
        answers = [
            _start(door, TENANT_A, STORES[0]),
            _start(door, TENANT_B, STORES[1]),
            _start(door, TENANT_A, STORES[2]),
        ]
        buckets = limiter.get_stats()["active_buckets"]

    assert [_started(answer) for answer in answers] == [True, True, False]
    _refused_by(answers[2], SHARED)
    assert buckets == 1


def test_an_idle_bucket_grants_only_what_it_refilled():
    with _served({"rps": NEVER, "burst": 2}) as (door, limiter):
        limiter.cleanup_interval = 0.05
        spent = [_hangar_call(door, TENANT_A) for _ in range(2)]
        time.sleep(0.2)  # idle past the cleanup window, with next to nothing refilled
        after = _hangar_call(door, TENANT_A)

    assert [call["success"] for call in spent] == [True, True]
    assert (after["success"], after["error_type"]) == (False, "RateLimitExceeded"), after
    assert after["error"].startswith(f"RateLimitExceeded: {SHARED} for InvokeToolCommand is used up"), after
