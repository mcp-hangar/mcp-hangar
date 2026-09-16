"""One shape for a rate-limit refusal, on every tool path the app serves (#1495).

Three checks can refuse a call, and each used to answer differently:

* the tool wrapper's own check (``RateLimited`` -> ``charge_tool``), which runs
  before the tool body -- ``hangar_fetch_continuation``. It raised out of the
  wrapper as an MCP error;
* ``charge_tool`` from inside a tool body, for work a tool does itself --
  ``hangar_start`` on a group, which starts its members rather than sending a
  command;
* the command bus's limiter -- ``hangar_start`` on a server, whose work is a
  ``StartMcpServerCommand``.

Every call goes over the real streamable-HTTP transport (``_front_door_harness``),
served in ``egress``, with the rate limit put in force by the function startup
uses. Nothing on the call path is patched. The security handler is substituted,
so what a refusal records can be read: ``caplog`` does not see these lines.

Naming: neutral placeholders only (store, read_item, tenant:a).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
from typing import Any

from mcp_hangar.bootstrap.runtime import install_command_bus_rate_limit
from mcp_hangar.domain.exceptions import RATE_LIMIT_CALLER
from mcp_hangar.domain.model.mcp_server_group import McpServerGroup
from mcp_hangar.domain.security.rate_limiter import reset_rate_limiter
from mcp_hangar.infrastructure.caller_rate_limit import reset_caller_rate_limit
from mcp_hangar.server.context import get_context
from tests.integration._front_door_harness import FrontDoor, front_door, jsonrpc, SERVER, TENANT_A

READ = "read_item"

#: A group whose one member is the server the harness already started, so
#: starting it is charged in the tool body without starting anything new.
GROUP = "store-group"

#: No continuation was ever produced; the tool body fails, which is enough to
#: spend the budget the wrapper's check charges before the body runs.
MISSING = "cont_none"

#: Tokens per second: nothing refills while a test runs.
NEVER = 0.001

#: One token per budget, so the second call on each key is refused.
ONE_EACH = {"rps": NEVER, "burst": 1, "per_caller": {"rps": NEVER, "burst": 1}}


class _Recorder:
    """Stands in for the security handler, keeping what it is told about a refusal."""

    def __init__(self) -> None:
        self.refusals: list[dict[str, Any]] = []
        self.validation_failures: list[str] = []

    def log_rate_limit_exceeded(
        self,
        mcp_server_id: str | None = None,
        limit: int = 0,
        window_seconds: int = 0,
        source_ip: str | None = None,
        *,
        scope: str = "",
        key_kind: str = "",
        key: str = "",
    ) -> None:
        self.refusals.append(
            {
                "scope": scope,
                "key_kind": key_kind,
                "key": key,
                "limit": limit,
                "mcp_server_id": mcp_server_id,
            }
        )

    def log_validation_failed(self, field: str, message: str, *_a: Any, **_k: Any) -> None:
        self.validation_failures.append(field)

    def __getattr__(self, _name: str) -> Any:
        """Every other security call is a no-op here."""
        return lambda *_a, **_k: None


@contextmanager
def _served(rate_limit: dict[str, Any]) -> Iterator[tuple[FrontDoor, _Recorder]]:
    """A gateway served in ``egress`` with *rate_limit* in force, and its security records."""
    reset_rate_limiter()
    try:
        with front_door((READ,), topology="egress") as door:
            runtime = get_context().runtime
            records = _Recorder()
            # `Runtime` is frozen and the app is already built, so the handler is
            # put in place here rather than passed to the builder. The bus reads
            # it when the middleware is installed, just below.
            object.__setattr__(runtime, "security_handler", records)
            install_command_bus_rate_limit(runtime, {"rate_limit": rate_limit}, env={})

            group = McpServerGroup(group_id=GROUP, auto_start=False)
            member = runtime.repository.get(SERVER)
            assert member is not None
            group.add_member(member)
            get_context().groups[GROUP] = group

            yield door, records
    finally:
        reset_caller_rate_limit()
        reset_rate_limiter()


def _answer(door: FrontDoor, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """What *name* answered, as the client reads it: one payload, never an MCP error."""
    payload = jsonrpc(door.call(TENANT_A, name, arguments))
    assert "result" in payload, payload
    return dict(json.loads(payload["result"]["content"][0]["text"]))


#: Each path, and the call that reaches it.
PATHS = {
    "the wrapper's check": ("hangar_fetch_continuation", {"continuation_id": MISSING}),
    "charge_tool in the tool body": ("hangar_start", {"mcp_server": GROUP}),
    "the command bus": ("hangar_start", {"mcp_server": SERVER}),
}

#: The budget each path's refusal names, in the order `PATHS` runs them.
KEYS = ["hangar_fetch_continuation", "hangar_start", "StartMcpServerCommand"]


def _record(key_kind: str, key: str) -> dict[str, Any]:
    """The record a refusal on *key* leaves: bounded fields, and nothing a caller chose."""
    return {"scope": RATE_LIMIT_CALLER, "key_kind": key_kind, "key": key, "limit": 1, "mcp_server_id": None}


def _spend(door: FrontDoor) -> None:
    """Spend the one token of every budget, so the next call on each is refused."""
    for name, arguments in PATHS.values():
        _answer(door, name, arguments)


def test_every_path_refuses_with_the_same_shape():
    with _served(ONE_EACH) as (door, _):
        _spend(door)
        refused = {path: _answer(door, *call) for path, call in PATHS.items()}

    for path, refusal in refused.items():
        assert sorted(refusal) == ["details", "error", "error_type"], (path, refusal)
        assert refusal["error_type"] == "RateLimitExceeded", (path, refusal)
        assert refusal["error"].startswith("RateLimitExceeded: this caller's rate limit"), (path, refusal)
        details = refusal["details"]
        # `code` is the `error_type` above; the rest keep the places they have
        # in a `hangar_call` result and over the HTTP API.
        assert details["scope"] == RATE_LIMIT_CALLER, (path, refusal)
        assert details["retry_after"] > 0, (path, refusal)
        assert (details["limit"], details["rps"]) == (1, NEVER), (path, refusal)

    assert [refusal["details"]["key"] for refusal in refused.values()] == KEYS


def test_every_refusal_is_recorded_once_the_bus_included():
    with _served(ONE_EACH) as (door, records):
        _spend(door)
        # The spending calls were not refused: only what follows is a refusal.
        records.refusals.clear()
        records.validation_failures.clear()
        for name, arguments in PATHS.values():
            _answer(door, name, arguments)
        recorded = list(records.refusals)
        as_validation = list(records.validation_failures)

    assert recorded == [
        _record("tool", "hangar_fetch_continuation"),
        _record("tool", "hangar_start"),
        _record("command", "StartMcpServerCommand"),
    ]
    # Bounded: the server a call named is not recorded, and a refusal is not
    # recorded a second time under another event type.
    assert as_validation == []
