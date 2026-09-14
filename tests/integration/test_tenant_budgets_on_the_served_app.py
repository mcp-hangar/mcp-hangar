"""Per-tenant execution budgets on the app ``serve --http`` serves (#1445).

Two tenants, each with its own API key, call over the real streamable-HTTP
transport (``_front_door_harness``). The budgets are configured from an
``execution.tenant_limits`` section, by the function startup and a reload
apply it with. Nothing on the call path is patched: the tenant a call is
charged to is the one its API key authenticated, bound again for each request
on this transport and carried into the executor's worker thread.

* Tenant A at its concurrency limit is refused while tenant B still runs, and
  the refusal is in the front-door call log and in ``/metrics``.
* A's rate budget refills over time.
* A call held for approval holds no slot: while it is held, another A call runs.
* A call the tool-access policy refuses spends no token.
* A call over its rate is refused before it is held for approval, and a
  tenant with no budget does not start a stopped server.
* A tenant that is not listed follows the documented rule: refused when there
  is no ``"*"`` entry, held to a budget of its own when there is one.
* With no ``tenant_limits``, nothing is refused.

Naming: neutral placeholders only (store, read_item, slow_item, held_item,
write_item, tenant:a, tenant:b).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import json
import re
import threading
import time
from typing import Any

import pytest

from mcp_hangar.approvals.delivery.noop import NoOpApprovalDelivery
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.fastmcp_server import flat_call_log
from mcp_hangar.server.config import _init_tenant_limits_from_config
from mcp_hangar.server.context import get_context
from mcp_hangar.server.tools.batch.executor import _close_approval_loops
from mcp_hangar.server.tools.batch.tenant_admission import get_tenant_admission, reset_tenant_admission
from tests.integration._front_door_harness import FrontDoor, front_door, jsonrpc, SERVER, TENANT_A, TENANT_B

READ = "read_item"
SLOW = "slow_item"
HELD = "held_item"
WRITE = "write_item"

IN_FLIGHT = "This tenant's execution budget is exhausted: too many calls in flight"
TOO_FAST = "This tenant's execution budget is exhausted: calls started too fast"
NO_BUDGET = "No execution budget is configured for this tenant"


def _budget(max_concurrency: int, *, rps: float = 1000, burst: int = 1000) -> dict[str, Any]:
    return {"max_concurrency": max_concurrency, "rps": rps, "burst": burst}


@pytest.fixture
def budgets() -> Iterator[Callable[[dict[str, Any]], None]]:
    """Configure ``execution.tenant_limits`` as startup and a reload do. Forgotten afterwards."""

    def configure(tenant_limits: dict[str, Any]) -> None:
        _init_tenant_limits_from_config({"execution": {"tenant_limits": tenant_limits}})

    try:
        yield configure
    finally:
        reset_tenant_admission()


class _CallLog:
    """Stands in for the front door's call logger, keeping each line it is given."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def info(self, event: str, **fields: Any) -> None:
        if event == flat_call_log.CALL_LOG_EVENT:
            self.lines.append(fields)


@pytest.fixture
def call_log(monkeypatch: pytest.MonkeyPatch) -> _CallLog:
    """The ``front_door_tool_call`` lines, as the front door writes them."""
    log = _CallLog()
    monkeypatch.setattr(flat_call_log, "logger", log)
    return log


def _served(door: FrontDoor, tenant: str, name: str) -> str:
    """The text of a call the gateway served."""
    return str(door.result(tenant, name)["content"][0]["text"])


def _refused(door: FrontDoor, tenant: str, name: str) -> str:
    """The text of a call the gateway refused as a tool error."""
    payload = jsonrpc(door.call(tenant, name))
    result = payload.get("result")
    assert isinstance(result, dict) and result.get("isError") is True, f"the call was not refused: {payload}"
    return str(result["content"][0]["text"])


def _hangar_call(door: FrontDoor, tenant: str, tool: str) -> dict[str, Any]:
    """The one call result of a ``hangar_call`` for *tool*, on a gateway served in ``egress``."""
    calls = [{"mcp_server": SERVER, "tool": tool, "arguments": {}}]
    payload = jsonrpc(door.call(tenant, "hangar_call", {"calls": calls}))
    assert "result" in payload, payload
    (call,) = json.loads(payload["result"]["content"][0]["text"])["results"]
    return dict(call)


_SAMPLE = re.compile(r"^mcp_hangar_tenant_quota_refusals_total\{(?P<labels>[^}]*)\} (?P<value>\S+)$")


def _refusals(door: FrontDoor, budget: str, reason: str) -> float:
    """The refusal counter for *budget* and *reason*, as ``/metrics`` exposes it."""
    for line in door.scrape().splitlines():
        sample = _SAMPLE.match(line)
        if sample and f'budget="{budget}"' in sample["labels"] and f'reason="{reason}"' in sample["labels"]:
            return float(sample["value"])
    return 0.0


def _wait_for(condition: Callable[[], object], what: str) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


@contextmanager
def _in_flight(door: FrontDoor, tenant: str, name: str) -> Iterator[Future[Any]]:
    """A call of *name* by *tenant* that the upstream holds until the block ends.

    Yields once the call has reached the upstream, so it holds its slot. The
    future is the call's response, which arrives after the block lets it go.
    """
    release = threading.Event()
    door.upstream.holds[name] = release
    arrived = door.upstream.called.count(name)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(door.call, tenant, name)
        _wait_for(lambda: door.upstream.called.count(name) > arrived, f"{name} to reach the upstream")
        yield future
    finally:
        release.set()
        pool.shutdown(wait=True)


class _Approvals:
    """An approval gate on the served context, over an in-memory store the test answers from."""

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRequest] = {}
        self.service = ApprovalGateService(
            repository=self,
            hold_registry=ApprovalHoldRegistry(),
            event_bus=self,
            delivery=NoOpApprovalDelivery(),
        )
        get_context().approval_gate = self.service

    # The event bus the service publishes to. Nothing here reads the events.
    def publish(self, event: Any) -> None:
        pass

    # The approval repository.
    async def save(self, request: ApprovalRequest) -> None:
        self._records[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._records.get(approval_id)

    async def list_pending(self, provider_id: str | None = None) -> list[ApprovalRequest]:
        return [record for record in self._records.values() if record.state == ApprovalState.PENDING]

    async def list_by_state(self, state: ApprovalState, provider_id: str | None = None) -> list[ApprovalRequest]:
        return [record for record in self._records.values() if record.state == state]

    async def update_state(
        self,
        approval_id: str,
        state: ApprovalState,
        decided_by: str | None,
        decided_at: datetime | None,
        reason: str | None,
    ) -> None:
        record = self._records[approval_id]
        record.state, record.decided_by, record.decided_at, record.reason = state, decided_by, decided_at, reason

    def pending(self) -> list[str]:
        return [record.approval_id for record in list(self._records.values()) if record.state == ApprovalState.PENDING]

    def approve(self, approval_id: str) -> None:
        assert asyncio.run(self.service.resolve(approval_id, True, "operator")), "the approval was not resolved"


class TestConcurrency:
    def test_a_tenant_at_its_limit_is_refused_while_another_tenant_runs(
        self, budgets: Callable[[dict[str, Any]], None], call_log: _CallLog
    ) -> None:
        budgets({TENANT_A: _budget(1), TENANT_B: _budget(1)})
        with front_door((READ, SLOW)) as door:
            before = _refusals(door, TENANT_A, "concurrency")
            with _in_flight(door, TENANT_A, SLOW) as held:
                refused = _refused(door, TENANT_A, READ)
                served_b = _served(door, TENANT_B, READ)
            first = jsonrpc(held.result(timeout=30))
            served_a = _served(door, TENANT_A, READ)  # its slot came back
            after = _refusals(door, TENANT_A, "concurrency")
            reached = list(door.upstream.called)

        assert refused == IN_FLIGHT
        assert served_b == served_a == f"did {READ}"
        assert not first["result"].get("isError") and first["result"]["content"][0]["text"] == f"did {SLOW}"
        assert reached == [SLOW, READ, READ], "the refused call reached the upstream"
        assert after - before == 1
        denied = [line for line in call_log.lines if line["outcome"] == flat_call_log.OUTCOME_DENIED]
        assert [(line["tool"], line["reason"], line["tenant_id"]) for line in denied] == [
            (READ, "TenantQuotaExceeded", TENANT_A)
        ]


class TestRate:
    def test_a_rate_budget_refills_over_time(self, budgets: Callable[[dict[str, Any]], None]) -> None:
        # One token, and one more every half second.
        budgets({TENANT_A: _budget(10, rps=2, burst=1), TENANT_B: _budget(10)})
        with front_door((READ,)) as door:
            first = _served(door, TENANT_A, READ)
            refused = _refused(door, TENANT_A, READ)
            served_b = _served(door, TENANT_B, READ)
            time.sleep(0.6)
            refilled = _served(door, TENANT_A, READ)

        assert refused == TOO_FAST
        assert first == served_b == refilled == f"did {READ}"


class TestWhatSpendsNothing:
    def test_a_call_held_for_approval_holds_no_slot(self, budgets: Callable[[dict[str, Any]], None]) -> None:
        budgets({TENANT_A: _budget(1), TENANT_B: _budget(1)})
        try:
            with front_door((READ, HELD)) as door:
                approvals = _Approvals()
                get_tool_access_resolver().set_standalone_member_policy(
                    SERVER, TENANT_A, ToolAccessPolicy(approval_list=(HELD,), approval_timeout_seconds=30)
                )
                with ThreadPoolExecutor(max_workers=1) as pool:
                    held = pool.submit(door.call, TENANT_A, HELD)
                    _wait_for(approvals.pending, "the call to be held for approval")
                    in_flight_while_held = get_tenant_admission().in_flight(TENANT_A)
                    # A's only slot is free while the other call waits for a human.
                    served = _served(door, TENANT_A, READ)
                    (approval_id,) = approvals.pending()
                    approvals.approve(approval_id)
                    answered = jsonrpc(held.result(timeout=30))
                reached = list(door.upstream.called)
        finally:
            # The hold waited on the executor's per-thread approval loop, whose
            # worker thread would otherwise outlive the suite's thread guard.
            _close_approval_loops()

        assert in_flight_while_held == 0
        assert served == f"did {READ}"
        assert not answered["result"].get("isError") and answered["result"]["content"][0]["text"] == f"did {HELD}"
        assert reached == [READ, HELD]

    def test_a_call_the_tool_access_policy_refuses_spends_no_token(
        self, budgets: Callable[[dict[str, Any]], None]
    ) -> None:
        # One token, and no second one within the test. On a front door the
        # refused tool is not projected at all; in egress it reaches the
        # executor, whose tool-access gate refuses it before the budget.
        budgets({TENANT_A: _budget(5, rps=0.001, burst=1)})
        with front_door((READ, WRITE), policies={TENANT_A: (READ,)}, topology="egress") as egress:
            denied = _hangar_call(egress, TENANT_A, WRITE)
            served = _hangar_call(egress, TENANT_A, READ)
            refused = _hangar_call(egress, TENANT_A, READ)
            reached = list(egress.upstream.called)

        assert (denied["success"], denied["error_type"]) == (False, "ToolAccessDeniedError"), denied
        assert served["success"] is True, served
        assert (refused["error_type"], refused["error"]) == ("TenantQuotaExceeded", TOO_FAST), refused
        assert reached == [READ]


class TestWhatIsRefusedEarly:
    def test_a_call_over_its_rate_is_refused_before_it_is_held_for_approval(
        self, budgets: Callable[[dict[str, Any]], None]
    ) -> None:
        budgets({TENANT_A: _budget(5, rps=0.001, burst=1)})
        try:
            with front_door((READ, HELD)) as door:
                approvals = _Approvals()
                get_tool_access_resolver().set_standalone_member_policy(
                    SERVER, TENANT_A, ToolAccessPolicy(approval_list=(HELD,), approval_timeout_seconds=30)
                )
                served = _served(door, TENANT_A, READ)
                refused = _refused(door, TENANT_A, HELD)
                pending = approvals.pending()
        finally:
            _close_approval_loops()

        assert served == f"did {READ}"
        assert refused == TOO_FAST
        assert pending == [], "a call that could not run was put to a human"

    def test_a_tenant_with_no_budget_does_not_start_a_stopped_server(
        self, budgets: Callable[[dict[str, Any]], None]
    ) -> None:
        budgets({TENANT_A: _budget(5)})
        with front_door((READ,)) as door:
            server = get_context().get_mcp_server(SERVER)
            assert server is not None
            server.stop()
            stopped = server.state.value
            refused = _refused(door, TENANT_B, READ)
            after_the_refusal = server.state.value
            served = _served(door, TENANT_A, READ)
            after_a_call = server.state.value

        assert (stopped, after_the_refusal) == ("cold", "cold")
        assert refused == NO_BUDGET
        assert (served, after_a_call) == (f"did {READ}", "ready")


class TestWhoIsHeldToWhichBudget:
    def test_without_a_default_entry_an_unlisted_tenant_is_refused(
        self, budgets: Callable[[dict[str, Any]], None]
    ) -> None:
        budgets({TENANT_A: _budget(5)})
        with front_door((READ,)) as door:
            before = _refusals(door, "none", "no_budget")
            refused = _refused(door, TENANT_B, READ)
            served = _served(door, TENANT_A, READ)
            after = _refusals(door, "none", "no_budget")
            reached = list(door.upstream.called)

        assert refused == NO_BUDGET
        assert served == f"did {READ}"
        assert reached == [READ]
        assert after - before == 1

    def test_with_a_default_entry_an_unlisted_tenant_has_a_budget_of_its_own(
        self, budgets: Callable[[dict[str, Any]], None]
    ) -> None:
        budgets({TENANT_A: _budget(5), "*": _budget(1)})
        with front_door((READ, SLOW)) as door:
            with _in_flight(door, TENANT_B, SLOW) as held:
                refused = _refused(door, TENANT_B, READ)
                served = _served(door, TENANT_A, READ)
            first = jsonrpc(held.result(timeout=30))

        assert refused == IN_FLIGHT
        assert served == f"did {READ}"
        assert not first["result"].get("isError")


def test_with_no_tenant_limits_nothing_is_refused() -> None:
    """The control: the same calls, from a configuration with no ``tenant_limits``."""
    _init_tenant_limits_from_config({"execution": {"max_concurrency": 10}})
    with front_door((READ, SLOW)) as door:
        with _in_flight(door, TENANT_A, SLOW) as held:
            served = [_served(door, TENANT_A, READ) for _ in range(3)] + [_served(door, TENANT_B, READ)]
        first = jsonrpc(held.result(timeout=30))

    assert served == [f"did {READ}"] * 4
    assert not first["result"].get("isError")
