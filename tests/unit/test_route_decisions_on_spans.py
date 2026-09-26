"""A call's trace says which backend served it, and why (#1286, ADR-029 s5).

For a call to group ``pool`` that selected ``member-b``, every span the executor
opened carried ``mcp.server.id=pool`` except two: ``mcp_server.cold_start`` and
``command.send.InvokeToolCommand`` carried ``member-b``. One attribute meant two
things inside one call, and nothing said why ``member-b`` was chosen.

Now ``mcp.server.id`` is the target the caller named on every span the executor
opens, the member is ``hangar.route.backend``, and ``batch.call.<tool>`` names
the reason from the selection that routed the call -- one selection per call,
retries included.

Driven through `BatchExecutor.execute` with real `McpServer` and
`McpServerGroup` objects in `GROUPS` and a real SDK provider, so the attributes
are the ones the executor really writes.

Naming: neutral placeholders only (pool, member-a, member-b, solo, tenant-*).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.model.mcp_server_group import CanaryPolicy, LoadBalancerStrategy, McpServerGroup
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.observability.conventions import Gate, Retry, Route
from mcp_hangar.retry import RetryPolicy
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec

pytestmark = pytest.mark.otel_sdk

_GROUP = "pool"
_A, _B = "member-a", "member-b"  # member-a is the load balancer's first choice
_SOLO = "solo"  # in no group
_UNLOADED = "unloaded"  # configured, no object loaded yet
_TOOL = "read"
_TENANT = "tenant-a"
_ID = "mcp.server.id"
_CALL = f"batch.call.{_TOOL}"
_COLD = "mcp_server.cold_start"
_SEND = "command.send.InvokeToolCommand"
#: Every span the executor opens for a call that runs, and ``invoke_with_retry`` under a retry policy.
_EXECUTOR_SPANS = (_CALL, "policy.check_access", "concurrency.acquire", _COLD, _SEND, "invoke_with_retry")


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


@pytest.fixture
def exporter() -> Iterator[Any]:
    """A real TracerProvider whose spans land in memory, patched into the executor."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=provider.get_tracer("test")):
        yield memory


def _group(*servers: McpServer) -> McpServerGroup:
    """A group whose members are in rotation, first member first, without starting anything."""
    group = McpServerGroup(group_id=_GROUP, strategy=LoadBalancerStrategy.PRIORITY, auto_start=False)
    for priority, server in enumerate(servers, start=1):
        group.add_member(server, priority=priority)
        member = group.get_member(str(server.id))
        assert member is not None
        member.in_rotation = True
    return group


@pytest.fixture()
def world():
    """`pool` = {member-a, member-b}, `solo` in no group, `unloaded` configured but not loaded; every gate open."""
    servers = {
        server_id: McpServer(mcp_server_id=server_id, mode="subprocess", command=["unused"])
        for server_id in (_A, _B, _SOLO)
    }
    group = _group(servers[_A], servers[_B])
    groups = {_GROUP: group}
    for server_id in (*servers, _UNLOADED):
        get_tool_projection_registry().build_from_tools(
            server_id, [ToolSchema(name=_TOOL, description=_TOOL, input_schema={})]
        )

    context = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.approval_gate = None
    context.auth_components = None
    context.get_mcp_server.side_effect = servers.get
    context.mcp_server_exists.side_effect = lambda server_id: server_id in servers or server_id == _UNLOADED
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS", groups),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS", groups),
    ):
        yield SimpleNamespace(context=context, group=group)


def _call(target: str, *, tenant: str | None = _TENANT) -> Any:
    identity = None
    if tenant is not None:
        caller = CallerIdentity(
            user_id=None, agent_id=None, session_id=None, principal_type="anonymous", tenant_id=tenant
        )
        identity = IdentityContext(caller=caller)
    token = identity_context_var.set(identity)
    try:
        batch_result = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=target, tool=_TOOL, arguments={})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
    finally:
        identity_context_var.reset(token)
    return batch_result.results[0]


def _spans(exporter: Any, name: str) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _one(exporter: Any, name: str) -> Any:
    (span,) = _spans(exporter, name)
    return span


def _route(exporter: Any) -> tuple[Any, Any]:
    attributes = _one(exporter, _CALL).attributes
    return attributes.get(Route.BACKEND), attributes.get(Route.REASON)


def _dispatched_to(context: Mock) -> list[str]:
    return [
        c.args[0].mcp_server_id
        for c in context.command_bus.send.call_args_list
        if isinstance(c.args[0], InvokeToolCommand)
    ]


def _assert_group_call_served_by(exporter: Any, member: str) -> None:
    """The group is `mcp.server.id` on every span the executor opened; the member is the backend."""
    for name in _EXECUTOR_SPANS:
        for span in _spans(exporter, name):
            assert span.attributes[_ID] == _GROUP, (name, dict(span.attributes))
    for name in (_CALL, _COLD, _SEND):
        assert _spans(exporter, name), name
        for span in _spans(exporter, name):
            assert span.attributes[Route.BACKEND] == member, (name, dict(span.attributes))


class TestMcpServerIdIsTheLogicalTarget:
    def test_cold_start_and_command_send_name_the_group_not_the_member(self, world, exporter):
        """The flip: these two spans carried the selected member in `mcp.server.id`."""
        assert _call(_GROUP).success is True

        assert _one(exporter, _COLD).attributes[_ID] == _GROUP
        assert _one(exporter, _SEND).attributes[_ID] == _GROUP

    def test_a_standalone_call_keeps_its_value(self, world, exporter):
        assert _call(_SOLO).success is True

        assert _one(exporter, _COLD).attributes[_ID] == _SOLO
        assert _one(exporter, _SEND).attributes[_ID] == _SOLO


class TestEachReasonReachesTheCallSpan:
    def test_load_balanced(self, world, exporter):
        assert _call(_GROUP).success is True

        assert _route(exporter) == (_A, "load_balanced")
        _assert_group_call_served_by(exporter, _A)
        assert _dispatched_to(world.context) == [_A]

    def test_load_balanced_under_a_canary_policy_when_the_caller_has_no_tenant(self, world, exporter):
        world.group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100))

        assert _call(_GROUP, tenant=None).success is True

        assert _route(exporter) == (_A, "load_balanced")
        _assert_group_call_served_by(exporter, _A)

    def test_pinned(self, world, exporter):
        world.group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: _B}))

        assert _call(_GROUP).success is True

        assert _route(exporter) == (_B, "pinned")
        _assert_group_call_served_by(exporter, _B)
        assert _dispatched_to(world.context) == [_B]

    def test_canary(self, world, exporter):
        world.group.set_canary_policy(CanaryPolicy(canary_member=_B, split_pct=100))

        assert _call(_GROUP).success is True

        assert _route(exporter) == (_B, "canary")
        _assert_group_call_served_by(exporter, _B)

    @pytest.mark.parametrize(
        "policy",
        [CanaryPolicy(canary_member=_B, split_pct=100), CanaryPolicy(pinned_tenants={_TENANT: _B})],
        ids=["canary", "pin"],
    )
    def test_canary_fallback_when_the_target_is_out_of_rotation(self, world, exporter, policy):
        world.group.set_canary_policy(policy)
        world.group.get_member(_B).in_rotation = False

        assert _call(_GROUP).success is True

        assert _route(exporter) == (_A, "canary_fallback")
        _assert_group_call_served_by(exporter, _A)

    def test_no_available_member_records_the_reason_and_no_backend(self, world, exporter):
        from opentelemetry.trace import StatusCode

        for member_id in (_A, _B):
            world.group.get_member(member_id).in_rotation = False

        result = _call(_GROUP)

        assert result.error_type == "NoAvailableMemberError"
        span = _one(exporter, _CALL)
        assert span.attributes[Route.REASON] == "no_available_member"
        assert Route.BACKEND not in span.attributes
        # The gate decision #1285 records is unchanged beside it.
        assert span.attributes[Gate.REFUSAL_GATE] == "resolve_target"
        assert span.attributes[Gate.REFUSAL_REASON] == "no_available_member"
        assert span.status.status_code is StatusCode.UNSET
        # Refused before anything downstream of the selection ran.
        assert not _spans(exporter, _COLD) and not _spans(exporter, _SEND)
        assert _dispatched_to(world.context) == []

    def test_standalone(self, world, exporter):
        assert _call(_SOLO).success is True

        assert _route(exporter) == (_SOLO, "standalone")
        for name in (_COLD, _SEND):
            attributes = _one(exporter, name).attributes
            assert attributes[_ID] == attributes[Route.BACKEND] == _SOLO, name

    def test_a_member_named_directly_is_standalone(self, world, exporter):
        """No group selected it, so there is no selection reason to give (decision 4 on #1286)."""
        spy = Mock(wraps=world.group.select_member_with_reason)
        with patch.object(world.group, "select_member_with_reason", spy):
            assert _call(_B).success is True

        spy.assert_not_called()
        assert _route(exporter) == (_B, "standalone")
        assert _one(exporter, _SEND).attributes[_ID] == _B
        assert _one(exporter, _SEND).attributes[Route.BACKEND] == _B

    def test_a_configured_server_not_yet_loaded_is_standalone(self, world, exporter):
        assert _call(_UNLOADED).success is True

        assert _route(exporter) == (_UNLOADED, "standalone")
        assert _one(exporter, _SEND).attributes[Route.BACKEND] == _UNLOADED

    def test_a_server_that_does_not_exist_records_no_route(self, world, exporter):
        assert _call("missing").error_type == "McpServerNotFoundError"

        assert _route(exporter) == (None, None)


class TestOneSelectionPerCall:
    @pytest.fixture
    def spy(self, world) -> Iterator[Mock]:
        spy = Mock(wraps=world.group.select_member_with_reason)
        with patch.object(world.group, "select_member_with_reason", spy):
            yield spy

    def test_a_group_call_selects_once(self, world, exporter, spy):
        _call(_GROUP)
        _call(_GROUP)

        assert spy.call_count == 2
        assert [c.args for c in spy.call_args_list] == [(_TENANT,), (_TENANT,)]

    def test_a_retried_call_selects_once_and_every_attempt_reaches_the_same_backend(
        self, world, exporter, spy, monkeypatch
    ):
        """Decision 3 on #1286: the dispatch target is fixed before the retries, so no attempt reselects."""
        monkeypatch.setattr("mcp_hangar.retry.time.sleep", lambda _seconds: None)
        world.group.set_canary_policy(CanaryPolicy(pinned_tenants={_TENANT: _B}))
        invokes = iter([ConnectionError("down"), ConnectionError("down"), {"ok": True}])

        def _send(command: Any) -> Any:
            if isinstance(command, InvokeToolCommand):
                outcome = next(invokes)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome
            return {"ok": True}

        world.context.command_bus.send.side_effect = _send
        policy = RetryPolicy(max_attempts=3, initial_delay=0.0, jitter=False)
        with patch("mcp_hangar.server.tools.batch.executor._retry_policy_for", return_value=policy):
            assert _call(_GROUP).success is True

        assert spy.call_count == 1
        sends = sorted(_spans(exporter, _SEND), key=lambda s: s.attributes[Retry.INDEX])
        assert [s.attributes[Retry.INDEX] for s in sends] == [1, 2, 3]
        assert {(s.attributes[_ID], s.attributes[Route.BACKEND]) for s in sends} == {(_GROUP, _B)}
        assert _dispatched_to(world.context) == [_B, _B, _B]
        assert _route(exporter) == (_B, "pinned")
        _assert_group_call_served_by(exporter, _B)


def test_recording_a_route_never_breaks_the_call(world, exporter, monkeypatch):
    """A telemetry failure must not change where the call goes."""

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("tracing broke")

    monkeypatch.setattr("mcp_hangar.observability.tracing._ambient_span", explode)

    assert _call(_GROUP).success is True
    assert _dispatched_to(world.context) == [_A]
