"""Tier 3 live verification: a group call's route reaches the exported spans (#1286).

BLACK-BOX, over the wire twice. A real ``mcp-hangar serve --http`` runs the
two-member group and canary policy of ``_group_support`` and exports through its
own OTLP gRPC exporter to the in-process receiver in ``_otlp_receiver``. Each
``hangar_call`` carries a real ``X-API-Key`` bound to a tenant, over
streamable-HTTP, and the members are ``examples/provider_identity`` subprocesses
whose ``whoami`` echoes which member served the call.

Proven, at the receiver:

- a pinned tenant reads ``hangar.route.reason=pinned``, a tenant in the canary
  split ``canary``, and a tenant outside it ``load_balanced``, each with the
  member that answered as ``hangar.route.backend``;
- ``mcp.server.id`` is the group on every span the executor opened for the call,
  ``mcp_server.cold_start`` and ``command.send.InvokeToolCommand`` included, and
  those two carry the member as ``hangar.route.backend``.

The route is keyed on the caller's tenant, which reaches the executor through
identity re-binds a mock context does not exercise. When it does not arrive,
this test FAILS: that is the fail-open this tier exists to catch, and a skip
would hide it (``test_t1_groups`` skips on that case). Not proven:
``canary_fallback`` and ``no_available_member``, which need a member taken out
of rotation mid-run; the unit and served-app tests cover them. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t3_route_decisions.py -m "live and t3" -o addopts=""
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from mcp_hangar.observability.conventions import Caller, McpServer, Route
from tests.live import _group_support as gs
from tests.live._otlp_receiver import OtlpReceiver, Received, poll
from tests.live.conftest import running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t3]

_ARRIVAL_TIMEOUT_S = 30.0
_CALL = "batch.call.whoami"
_COLD = "mcp_server.cold_start"
_SEND = "command.send.InvokeToolCommand"
#: The spans the executor opens for a call; `mcp.server.id` is the logical target on each.
_EXECUTOR_SPANS = frozenset(
    {_CALL, "policy.check_access", "approval_gate.check", "concurrency.acquire", _COLD, _SEND, "invoke_with_retry"}
)

_PINNED = "tenant:pin-b"  # pinned to member-b, which is also the canary: a pin must win
#: The first tenants the split sends to the canary, and keeps on the load balancer.
_IN_SPLIT = next(t for t in gs.SPLIT_TENANTS if gs.expected_split_target(t) == gs.CANARY_MEMBER)
_OUT_OF_SPLIT = next(t for t in gs.SPLIT_TENANTS if gs.expected_split_target(t) is None)


@dataclass
class _Harness:
    receiver: OtlpReceiver
    group: gs.GroupHarness
    run_id: str


@pytest.fixture(scope="module")
def harness(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not gs.IDENTITY_SERVER.exists():
        pytest.skip(f"identity stub backend not found at {gs.IDENTITY_SERVER}")

    workdir = tmp_path_factory.mktemp("route_decisions")
    auth_db = workdir / "auth.db"
    keys = gs.seed_tenant_keys(auth_db, [_PINNED, _IN_SPLIT, _OUT_OF_SPLIT])

    receiver = OtlpReceiver()
    try:
        run_id = uuid.uuid4().hex
        env = {k: v for k, v in os.environ.items() if not k.startswith(("OTEL_", "MCP_TRACING"))}
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = receiver.endpoint  # http:// -- plaintext gRPC
        env["OTEL_RESOURCE_ATTRIBUTES"] = f"service.instance.id={run_id}"
        with running_hangar(workdir, gs.render_config(auth_db), env) as hangar:
            group = gs.GroupHarness(base_url=hangar.base_url, tenant_keys=keys)
            yield _Harness(receiver=receiver, group=group, run_id=run_id)
    finally:
        receiver.stop()


def _served_calls(harness: _Harness, tenant: str) -> tuple[str, list[tuple[Received, list[Received]]]]:
    """Call ``whoami`` as *tenant*: the member that answered, and each call span it made with its trace."""
    served = gs.serving_member(harness.group, tenant_id=tenant)
    assert served in gs.MEMBERS, f"{tenant}: no member served the call ({served!r})"

    def _probe() -> list[tuple[Received, list[Received]]] | None:
        spans = harness.receiver.spans(harness.run_id)
        calls = [s for s in spans if s.name == _CALL and s.attributes.get(Caller.TENANT) == tenant]
        # The call's own `command.send` ends before its `batch.call`; wait for the call span.
        if not calls:
            return None
        return [(call, [s for s in spans if s.trace_id == call.trace_id]) for call in calls]

    found = poll(_probe, _ARRIVAL_TIMEOUT_S)
    assert found is not None, (
        f"no {_CALL} span carrying {Caller.TENANT}={tenant!r} reached the receiver: the tenant the key "
        "authenticated did not reach the executor, which is the fail-open this test exists to catch"
    )
    return served, found


def _assert_route(harness: _Harness, tenant: str, reason: str, member: str | None = None) -> None:
    """*tenant*'s served call reads *reason*, on *member* when given, else on whichever member answered."""
    served, calls = _served_calls(harness, tenant)
    member = member or served
    assert served == member, f"{tenant}: {served} answered, the route says {member}"
    for call, trace in calls:
        if call.attributes.get("hangar.call.outcome") != "allow":
            continue  # a cold-start miss the client retried; the served attempt is asserted
        assert call.attributes.get(Route.REASON) == reason, (
            f"{tenant}: expected {reason!r}, got {call.attributes.get(Route.REASON)!r}. A per-tenant route that "
            "reads load_balanced means the tenant never reached member selection."
        )
        assert call.attributes.get(Route.BACKEND) == member, call.attributes
        executor_spans = [s for s in trace if s.name in _EXECUTOR_SPANS]
        assert {s.attributes.get(McpServer.ID) for s in executor_spans} == {gs.GROUP_ID}, [
            (s.name, s.attributes.get(McpServer.ID)) for s in executor_spans
        ]
        sends = [s for s in trace if s.name in (_COLD, _SEND)]
        assert any(s.name == _SEND for s in sends), [s.name for s in trace]
        assert {s.attributes.get(Route.BACKEND) for s in sends} == {member}, [
            (s.name, s.attributes.get(Route.BACKEND)) for s in sends
        ]
        return
    raise AssertionError(f"{tenant}: no allowed {_CALL} span reached the receiver")


def test_a_pinned_tenant_reads_pinned(harness: _Harness) -> None:
    _assert_route(harness, _PINNED, "pinned", gs.PINNED[_PINNED])


def test_a_tenant_in_the_canary_split_reads_canary(harness: _Harness) -> None:
    _assert_route(harness, _IN_SPLIT, "canary", gs.CANARY_MEMBER)


def test_a_tenant_outside_the_split_is_load_balanced_on_the_member_that_answered(harness: _Harness) -> None:
    _assert_route(harness, _OUT_OF_SPLIT, "load_balanced")
