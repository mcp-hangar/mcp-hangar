"""A group member is judged by its own health, not by the caller's errors (#1409).

`member_health.member_outcome` is the one place that decides what a call's
outcome says about the member that took it. Before #1409 every call through a
group that did not succeed counted against the member, so a caller dividing by
zero took a healthy member out of rotation.

Each outcome class has a test here, and every refusal code the executor writes
is sorted into exactly one of two sets, so a new one is classified on purpose.
The executor's reporting paths, a gate's refusal and an invocation's outcome,
are driven through ``BatchExecutor.execute`` with a group that records what it
hears. What a real ``McpServer`` raises is read too: the verdict depends on the
JSON-RPC code the aggregate puts on its error.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest

from mcp_hangar.application.commands import InvokeToolCommand, StartMcpServerCommand
from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.domain import exceptions as domain_exceptions
from mcp_hangar.domain.exceptions import (
    CannotStartMcpServerError,
    CapabilityBlockedError,
    ClientError,
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    McpServerDegradedError,
    McpServerNotReadyError,
    McpServerStartError,
    RateLimitExceeded,
    ToolAccessDeniedError,
    ToolInvocationError,
    ToolNotFoundError,
    ToolTimeoutError,
)
from mcp_hangar.domain.model import McpServer, McpServerState
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import reset_tool_access_resolver
from mcp_hangar.fastmcp_server.flat_call_log import DENIAL_CODES
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch import executor as executor_module
from mcp_hangar.server.tools.batch.member_health import (
    MEMBER_HEALTH_REFUSALS,
    NOT_MEMBER_REFUSALS,
    MemberOutcome,
    member_outcome,
)

HEALTHY, UNHEALTHY, UNJUDGED = MemberOutcome.HEALTHY, MemberOutcome.UNHEALTHY, MemberOutcome.UNJUDGED


def _raised(cls: type[BaseException]) -> BaseException:
    """An instance of *cls* as the verdict reads it: by its type alone."""
    return cls.__new__(cls)


def _rpc_error(code: object) -> ToolInvocationError:
    """What a member raises for an upstream JSON-RPC error, as `McpServer.invoke_tool` builds it."""
    return ToolInvocationError("svc", "tool_error: no", {"tool_name": "t", "correlation_id": "c", "jsonrpc_code": code})


def _transport(cause: BaseException) -> ToolInvocationError:
    """What a member raises when the transport fails under a call."""
    error = ToolInvocationError("svc", str(cause), {"tool_name": "t", "correlation_id": "c"})
    error.__cause__ = cause
    return error


#: What a member raises for a tool result with ``isError: true``.
TOOL_LEVEL_ERROR = ToolInvocationError(
    "svc", "tool_error: no", {"tool_name": "t", "correlation_id": "c", "is_error": True, "content": []}
)


# ----------------------------------------------------------------------------
# The verdict, one outcome class at a time
# ----------------------------------------------------------------------------


class TestTheMemberAnswered:
    """A working exchange about a bad request: the group hears a success."""

    def test_a_result_marked_is_error(self) -> None:
        assert member_outcome(TOOL_LEVEL_ERROR) is HEALTHY

    @pytest.mark.parametrize("code", [-32602, -32601])
    def test_invalid_params_or_a_method_the_member_does_not_have(self, code: int) -> None:
        assert member_outcome(_rpc_error(code)) is HEALTHY

    @pytest.mark.parametrize("code", [-1, 0, 1, 42, -31999, -32769])
    def test_a_code_outside_the_reserved_range_is_the_tools_own(self, code: int) -> None:
        assert member_outcome(_rpc_error(code)) is HEALTHY


class TestEvidenceAgainstTheMember:
    """What the group hears as a failure, as it did before #1409."""

    @pytest.mark.parametrize("code", [-32700, -32600, -32603, -32000, -32099, -32768])
    def test_a_protocol_or_server_error(self, code: int) -> None:
        assert member_outcome(_rpc_error(code)) is UNHEALTHY

    @pytest.mark.parametrize("code", [None, "-32602", 1.5, True])
    def test_an_error_without_an_integer_code(self, code: object) -> None:
        assert member_outcome(_rpc_error(code)) is UNHEALTHY

    @pytest.mark.parametrize("cause", [OSError("broken pipe"), TimeoutError("timeout: tools/call after 1s")])
    def test_the_transport_failed_or_timed_out(self, cause: BaseException) -> None:
        assert member_outcome(_transport(cause)) is UNHEALTHY
        assert member_outcome(cause) is UNHEALTHY

    def test_no_response_or_no_client(self) -> None:
        assert member_outcome(ToolInvocationError("svc", "No response from mcp_server")) is UNHEALTHY

    @pytest.mark.parametrize(
        "cls",
        [
            ClientError,
            ToolTimeoutError,
            McpServerStartError,
            CapabilityBlockedError,
            CannotStartMcpServerError,
            McpServerNotReadyError,
            McpServerDegradedError,
        ],
    )
    def test_the_member_could_not_take_the_call(self, cls: type[BaseException]) -> None:
        assert member_outcome(_raised(cls)) is UNHEALTHY

    def test_an_error_nobody_classified_counts_as_before(self) -> None:
        assert member_outcome(RuntimeError("backend down")) is UNHEALTHY

    def test_a_failure_that_recorded_no_error_counts(self) -> None:
        assert member_outcome(None) is UNHEALTHY

    @pytest.mark.parametrize("code", sorted(MEMBER_HEALTH_REFUSALS))
    def test_a_refusal_for_the_members_own_state(self, code: str) -> None:
        assert member_outcome(code) is UNHEALTHY


class TestHangarDecided:
    """Nothing reached the member, so the group hears nothing."""

    @pytest.mark.parametrize(
        "cls",
        [
            ToolAccessDeniedError,
            EgressPolicyDeniedError,
            EgressPolicyApprovalRequiredError,
            RateLimitExceeded,
            ToolNotFoundError,
        ],
    )
    def test_a_refusal_raised_before_the_member_is_asked(self, cls: type[BaseException]) -> None:
        assert member_outcome(_raised(cls)) is UNJUDGED

    @pytest.mark.parametrize("code", sorted(NOT_MEMBER_REFUSALS))
    def test_a_gates_refusal(self, code: str) -> None:
        assert member_outcome(code) is UNJUDGED

    @pytest.mark.parametrize("code", sorted(DENIAL_CODES))
    def test_a_denial_never_counts(self, code: str) -> None:
        assert member_outcome(code) is UNJUDGED

    def test_an_error_named_as_a_denial_is_hangars_own_refusal(self) -> None:
        named = [
            cls
            for code in sorted(DENIAL_CODES)
            if isinstance(cls := getattr(domain_exceptions, code, None), type) and issubclass(cls, BaseException)
        ]

        assert {cls.__name__ for cls in named} >= {
            "ToolAccessDeniedError",
            "EgressPolicyDeniedError",
            "EgressPolicyApprovalRequiredError",
        }
        for cls in named:
            assert member_outcome(_raised(cls)) is UNJUDGED, cls


# ----------------------------------------------------------------------------
# A new refusal code is sorted on purpose
# ----------------------------------------------------------------------------


def _codes_the_executor_writes() -> set[str]:
    """Every constant refusal code in the executor, as the front-door call log's own test reads them."""
    written: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(executor_module))):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if callee in ("refuse", "_refuse") and node.args and isinstance(node.args[-1], ast.Constant):
            written.add(node.args[-1].value)
        written |= {
            kw.value.value
            for kw in node.keywords
            if kw.arg == "error_type" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
        }
    return written


class TestEveryRefusalIsSortedOnPurpose:
    def test_the_two_sets_do_not_overlap(self) -> None:
        assert not MEMBER_HEALTH_REFUSALS & NOT_MEMBER_REFUSALS

    def test_every_code_the_executor_writes_is_in_one_of_them(self) -> None:
        # A new refusal code goes into MEMBER_HEALTH_REFUSALS if it reports the
        # member's own state, or NOT_MEMBER_REFUSALS if it does not. Left out,
        # it would count for nothing, whatever it says about the member.
        unsorted = _codes_the_executor_writes() - (MEMBER_HEALTH_REFUSALS | NOT_MEMBER_REFUSALS)

        assert unsorted == set()

    def test_every_code_that_counts_is_one_the_executor_writes(self) -> None:
        assert _codes_the_executor_writes() >= MEMBER_HEALTH_REFUSALS


# ----------------------------------------------------------------------------
# What a real member raises carries what the verdict reads
# ----------------------------------------------------------------------------


def _raised_by_a_member(
    response: dict[str, Any] | None = None, error: BaseException | None = None
) -> ToolInvocationError:
    """Call tool ``t`` on a READY `McpServer` whose client answers *response* or raises *error*."""
    member = McpServer(mcp_server_id="svc", mode="subprocess", command=["python", "-m", "test"])
    client = MagicMock()
    client.is_alive.return_value = True
    client.call.return_value = response
    client.call.side_effect = error
    with member._lock:
        member._state = McpServerState.READY
        member._client = client
    member._tools.add(ToolSchema(name="t", description="t", input_schema={}))

    with pytest.raises(ToolInvocationError) as raised:
        member.invoke_tool("t", {})
    return raised.value


class TestWhatAMemberRaises:
    @pytest.mark.parametrize(
        ("code", "outcome"), [(-32602, HEALTHY), (-1, HEALTHY), (-32603, UNHEALTHY), (-32000, UNHEALTHY)]
    )
    def test_a_jsonrpc_error_carries_its_code(self, code: int, outcome: MemberOutcome) -> None:
        error = _raised_by_a_member({"error": {"code": code, "message": "no"}})

        assert error.details["jsonrpc_code"] == code
        assert member_outcome(error) is outcome

    def test_a_code_that_is_not_an_integer_is_not_copied(self) -> None:
        error = _raised_by_a_member({"error": {"code": "-32602", "message": "no"}})

        assert error.details["jsonrpc_code"] is None
        assert member_outcome(error) is UNHEALTHY

    def test_a_result_marked_is_error(self) -> None:
        error = _raised_by_a_member({"result": {"content": [{"type": "text", "text": "no"}], "isError": True}})

        assert member_outcome(error) is HEALTHY

    @pytest.mark.parametrize("cause", [OSError("broken pipe"), TimeoutError("timeout: tools/call after 1s")])
    def test_a_transport_failure(self, cause: BaseException) -> None:
        assert member_outcome(_raised_by_a_member(error=cause)) is UNHEALTHY


# ----------------------------------------------------------------------------
# The executor tells the group the verdict, on both of its paths
# ----------------------------------------------------------------------------

_GROUP, _MEMBER, _TOOL = "pool", "svc", "t"


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()


def _member(state: str) -> Mock:
    member = Mock()
    member.id = Mock(value=_MEMBER)
    member.state = Mock(value=state)
    member.health = Mock(should_degrade=Mock(return_value=False))
    return member


GroupCall = Callable[..., tuple[Any, list[str]]]


@pytest.fixture()
def group_call() -> Iterator[GroupCall]:
    """Run one call through a group; its member's commands raise as the test says."""
    ctx = Mock()
    ctx.get_mcp_server.return_value = None  # a group is not in the server repository
    ctx.mcp_server_exists.return_value = False
    group = Mock()
    # The executor drains the group after reporting to it (#1410). A real
    # aggregate hands back a list; this double is only asked what it heard.
    group.collect_events.return_value = []

    def run(
        invoke: BaseException | None = None, start: BaseException | None = None, state: str = "ready", retries: int = 1
    ) -> tuple[Any, list[str]]:
        group.select_member_for.return_value = _member(state)

        def send(command: Any) -> Any:
            if isinstance(command, StartMcpServerCommand) and start is not None:
                raise start
            if isinstance(command, InvokeToolCommand) and invoke is not None:
                raise invoke
            return {"ok": True}

        ctx.command_bus.send.side_effect = send
        call = CallSpec(index=0, call_id="c1", mcp_server=_GROUP, tool=_TOOL, arguments={}, max_retries=retries)
        batch = BatchExecutor().execute(
            batch_id="b", calls=[call], max_concurrency=1, global_timeout=30.0, fail_fast=False
        )
        heard = [name for name, _args, _kwargs in group.method_calls if name in ("report_success", "report_failure")]
        return batch.results[0], heard

    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as executor_groups,
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as validator_groups,
    ):
        executor_groups.get.return_value = group
        validator_groups.get.return_value = group
        yield run


class TestTheExecutorTellsTheGroup:
    def test_a_success(self, group_call: GroupCall) -> None:
        result, heard = group_call()

        assert result.success is True and heard == ["report_success"]

    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (TOOL_LEVEL_ERROR, ["report_success"]),
            (_rpc_error(-32602), ["report_success"]),
            (_rpc_error(-1), ["report_success"]),
            (_rpc_error(-32603), ["report_failure"]),
            (_transport(OSError("broken pipe")), ["report_failure"]),
            (RuntimeError("backend down"), ["report_failure"]),
            (RateLimitExceeded(limit=1, window_seconds=1), []),
            (ToolNotFoundError(_MEMBER, _TOOL), []),
        ],
        ids=[
            "is_error",
            "invalid_params",
            "application_code",
            "internal_error",
            "transport",
            "unclassified",
            "rate_limited",
            "not_in_catalogue",
        ],
    )
    def test_a_failed_invocation(self, group_call: GroupCall, raised: BaseException, expected: list[str]) -> None:
        result, heard = group_call(invoke=raised)

        assert result.success is False
        assert heard == expected

    def test_an_invocation_that_retried_is_judged_by_its_last_error(self, group_call: GroupCall) -> None:
        result, heard = group_call(invoke=_rpc_error(-32602), retries=2)

        assert result.success is False and result.retry_metadata is not None
        assert heard == ["report_success"]

    def test_a_start_the_rate_limit_refused_is_not_the_members_failure(self, group_call: GroupCall) -> None:
        result, heard = group_call(start=RateLimitExceeded(limit=1, window_seconds=1), state="cold")

        assert result.error_type == "McpServerStartError"
        assert heard == []

    def test_a_start_that_failed_is(self, group_call: GroupCall) -> None:
        result, heard = group_call(start=McpServerStartError(_MEMBER, "the process exited"), state="cold")

        assert result.error_type == "McpServerStartError"
        assert heard == ["report_failure"]
