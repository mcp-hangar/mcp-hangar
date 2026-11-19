"""A ``notifications/cancelled`` that arrives as a request of its own cancels the calls it names (#2289).

Over streamable HTTP on the handshake era the gateway keeps no session (#877),
so a client's cancel is a POST that reaches no request. Each held call's
approval record now names the caller's request -- its JSON-RPC id and the
batch it made -- and the cancel finds the pending calls there:

- the calls of one request of this principal and tenant move to ``cancelled``,
  and a hold on this replica is woken at once;
- another principal, another tenant, no principal, or an id two requests share
  cancels nothing, and the holds go on as before;
- a call approved before the cancel is not pending, so it is left to run;
- the batch's later calls are not held for a caller that has left.

The record is read through the real SQLite repository, so the two columns are
covered on the way through. The PostgreSQL repository stores them the same way.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.persistence.sqlite_approval_repository import SqliteApprovalRepository
from mcp_hangar.approvals.service import ABANDONED_REASON, ApprovalGateService
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.fastmcp_server.client_cancel import cancelled_request_id
from mcp_hangar.infrastructure.persistence.database import Database, DatabaseConfig
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, ClientRequest
from tests.unit.test_an_approval_hold_leaves_nothing_behind import SERVER, TOOL, _hold_for, _Store, served_world


@pytest.fixture
def service(tmp_path: Path) -> ApprovalGateService:
    database = Database(DatabaseConfig(path=str(tmp_path / "approvals.db")))
    asyncio.run(database.initialize())
    gate = ApprovalGateService.__new__(ApprovalGateService)
    gate._repository = SqliteApprovalRepository(database)
    gate._hold_registry = ApprovalHoldRegistry()
    return gate


def _held(
    service: ApprovalGateService,
    approval_id: str,
    *,
    by: str | None = "svc:a",
    tenant: str | None = "t-a",
    request_id: str = "2",
    batch: str = "b-1",
    state: ApprovalState = ApprovalState.PENDING,
) -> None:
    now = datetime.now(UTC)
    record = ApprovalRequest(
        approval_id=approval_id,
        mcp_server_id="demo",
        tool_name="send_message",
        arguments={},
        arguments_hash="h",
        requested_at=now,
        expires_at=now + timedelta(minutes=1),
        state=state,
        channel="event_stream",
        requested_by=by,
        tenant_id=tenant,
        client_request_id=request_id,
        batch_id=batch,
    )
    asyncio.run(service._repository.save(record))


def _state(service: ApprovalGateService, approval_id: str) -> ApprovalState:
    record = asyncio.run(service._repository.get(approval_id))
    assert record is not None
    return record.state


def _cancel(service: ApprovalGateService, by: str | None = "svc:a", tenant: str | None = "t-a", request_id: str = "2"):
    return asyncio.run(service.cancel_client_request(by, tenant, request_id))


class TestTheCancelFindsTheCallsOfItsRequest:
    def test_every_pending_call_of_the_request_is_cancelled(self, service: ApprovalGateService) -> None:
        _held(service, "a-1")
        _held(service, "a-2")

        assert sorted(_cancel(service)) == ["a-1", "a-2"]

        for approval_id in ("a-1", "a-2"):
            record = asyncio.run(service._repository.get(approval_id))
            assert record is not None
            assert (record.state, record.reason) == (ApprovalState.CANCELLED, ABANDONED_REASON)

    def test_a_hold_on_this_replica_is_woken_with_a_refusal(self, service: ApprovalGateService) -> None:
        _held(service, "a-1")

        async def run() -> bool | None:
            await service._hold_registry.register("a-1")
            await service.cancel_client_request("svc:a", "t-a", "2")
            return await service._hold_registry.wait_slice("a-1", 1.0)

        assert asyncio.run(run()) is False

    def test_a_call_approved_first_is_left_to_run(self, service: ApprovalGateService) -> None:
        _held(service, "a-1", state=ApprovalState.APPROVED)

        assert _cancel(service) == []
        assert _state(service, "a-1") is ApprovalState.APPROVED


class TestACancelThatNamesNoOneRequestDoesNothing:
    @pytest.mark.parametrize(
        ("by", "tenant", "request_id"),
        [
            ("svc:b", "t-a", "2"),
            ("svc:a", "t-b", "2"),
            ("svc:a", None, "2"),
            ("svc:a", "t-a", '"2"'),
            (None, "t-a", "2"),
            ("", "t-a", "2"),
        ],
        ids=["another-principal", "another-tenant", "no-tenant", "string-id", "no-principal", "empty-principal"],
    )
    def test_a_cancel_from_anyone_else_or_for_another_id(
        self, service: ApprovalGateService, by: str | None, tenant: str | None, request_id: str
    ) -> None:
        _held(service, "a-1")

        assert _cancel(service, by, tenant, request_id) == []
        assert _state(service, "a-1") is ApprovalState.PENDING

    def test_an_id_two_requests_share(self, service: ApprovalGateService) -> None:
        """Two connections of one principal each have a request 2 held: the cancel cannot tell which it meant."""
        _held(service, "a-1", batch="b-1")
        _held(service, "a-2", batch="b-2")

        assert _cancel(service) == []
        assert {_state(service, "a-1"), _state(service, "a-2")} == {ApprovalState.PENDING}

    def test_a_call_recorded_without_its_request(self, service: ApprovalGateService) -> None:
        """A record from before the columns existed, or from stdio, has no batch: nothing matches it."""
        _held(service, "a-1", batch=None)  # type: ignore[arg-type]

        assert _cancel(service) == []


class TestTheCancelBodyIsRead:
    @pytest.mark.parametrize(
        ("body", "named"),
        [
            (b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2}}', "2"),
            (b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":"2"}}', '"2"'),
            (b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":true}}', None),
            (b'{"jsonrpc":"2.0","method":"notifications/cancelled","params":{}}', None),
            (b'{"jsonrpc":"2.0","id":3,"method":"notifications/cancelled","params":{"requestId":2}}', None),
            (b'{"jsonrpc":"2.0","method":"notifications/initialized"}', None),
            (b'[{"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":2}}]', None),
            (b"not json", None),
        ],
        ids=["int", "string", "bool", "no-id", "a-request", "another-method", "a-batch", "not-json"],
    )
    def test_only_a_cancel_names_a_request(self, body: bytes, named: str | None) -> None:
        assert cancelled_request_id(body) == named


class TestTheCallNamesItsRequest:
    def test_over_http_the_request_id_is_kept_as_json(self) -> None:
        assert ClientRequest.of(SimpleNamespace(request=object(), request_id=2), "b") == ClientRequest("2", "b")
        assert ClientRequest.of(SimpleNamespace(request=object(), request_id="2"), "b") == ClientRequest('"2"', "b")

    def test_a_tool_context_is_read_through_its_request_context(self) -> None:
        tool_ctx = SimpleNamespace(request_context=SimpleNamespace(request=object(), request_id=7))

        assert ClientRequest.of(tool_ctx, "b") == ClientRequest("7", "b")

    @pytest.mark.parametrize(
        "ctx",
        [None, SimpleNamespace(request=None, request_id=2), SimpleNamespace(request=object(), request_id=None)],
        ids=["no-context", "stdio", "no-id"],
    )
    def test_without_an_http_request_there_is_nothing_to_name(self, ctx: Any) -> None:
        assert ClientRequest.of(ctx, "b") is None


class _ListingStore(_Store):
    async def list_by_state(self, state: ApprovalState, provider_id: str | None = None) -> list[ApprovalRequest]:
        return [r for r in self._rows.values() if r.state is state]


def test_the_batch_holds_no_later_call_for_a_caller_that_left() -> None:
    """Two calls of one request, one after the other: the cancel ends the first hold and the second is not held."""
    with served_world() as world:
        world.approver.answer = None
        service = world.approver.service
        assert service is not None
        service._repository = _ListingStore()
        _hold_for(30)
        caller_gone = threading.Event()
        request = ClientRequest("2", "b")
        specs = [
            CallSpec(
                index=i,
                call_id=f"c-{i}",
                mcp_server=SERVER,
                tool=TOOL,
                arguments={"amount": i},
                caller_gone=caller_gone,
                client_request=request,
            )
            for i in range(2)
        ]
        loop = asyncio.new_event_loop()

        def cancel_once_held() -> None:
            assert world.approver.wait_until_held(1, timeout=10)
            assert loop.run_until_complete(service.cancel_client_request("svc:a", "t-a", "2")) == [
                world.approver.approval_ids[0]
            ]

        token = identity_context_var.set(
            IdentityContext(caller=CallerIdentity(user_id="svc:a", agent_id=None, session_id=None, tenant_id="t-a"))
        )
        canceller = threading.Thread(target=cancel_once_held)
        canceller.start()
        try:
            batch = BatchExecutor().execute(
                batch_id="b", calls=specs, max_concurrency=1, global_timeout=30.0, fail_fast=False
            )
        finally:
            identity_context_var.reset(token)
            canceller.join(timeout=10)
            loop.close()

        assert caller_gone.is_set()
        assert [r.success for r in batch.results] == [False, False]
        states = [asyncio.run(service._repository.get(a)).state for a in world.approver.approval_ids]  # type: ignore[union-attr]
        assert set(states) <= {ApprovalState.CANCELLED}
