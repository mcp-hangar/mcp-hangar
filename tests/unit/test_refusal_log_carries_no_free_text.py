"""A refused call is logged with its bounded reason, never with the gate's free text (#1581).

`batch_call_refused` carried `error`, the message the refusing gate wrote for
the caller. Several gates fill that message from text they do not bound: the
approval gate with the approver's own reason, the validators gate with the
validator's, the target gate with the server name the caller typed, the pin and
withdrawal gates with the tool name. The data-handling contract allows a log
line event type, identifiers and bounded codes (#1276), and ADR-029 s5 says a
refusal logs "with its bounded reason": `reason` and `error_type`, which the
same line already carries.

The caller is still told why, in the tool result. Only the log changes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
import structlog

from mcp_hangar.application.services.validator_pipeline import ValidatorPipeline
from mcp_hangar.approvals.models import ApprovalResult
from mcp_hangar.domain.contracts.validator import ValidationResult
from mcp_hangar.domain.exceptions import CannotStartMcpServerError
from mcp_hangar.domain.model.mcp_server import START_REFUSED_NOT_REVIVED_BY_CALLS
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch.executor import _GATES, _log_gate_outcome
from mcp_hangar.server.tools.batch.models import CallResult
from mcp_hangar.server.tools.batch.tenant_admission import (
    TenantLimits,
    configure_tenant_limits,
    reset_tenant_admission,
)
from tests.unit import test_batch_gate_precedence as precedence
from tests.unit.test_batch_gate_precedence import (
    _SERVER,
    _TENANT,
    _TOOL,
    _arrange_catalogue,
    _arrange_circuit_open,
    _arrange_deferred_pin,
    _arrange_server_missing,
    _arrange_stale_pin,
    _arrange_tool_access_denied,
    _arrange_withdrawn,
    _run,
)

ctx = precedence.ctx
_reset_singletons = precedence._reset_singletons

#: Planted wherever a gate takes text from outside itself.
CANARY = "canary-7f3e-free-text-from-outside-the-gate"


class _DenyingApprover:
    """An approval gate whose approver says no, and says why in their own words."""

    async def check(self, **_kwargs: Any) -> ApprovalResult:
        return ApprovalResult.denied("approval-1", reason=CANARY)


@contextmanager
def _budget(limits: dict[str, TenantLimits]) -> Iterator[None]:
    configure_tenant_limits(limits)
    try:
        yield
    finally:
        reset_tenant_admission()


def _server_missing(ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    _arrange_server_missing(ctx)
    return {}


def _tool_access(_ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    _arrange_tool_access_denied()
    return {}


def _withdrawn(_ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    _arrange_withdrawn()
    return {}


def _stale_pin(_ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    _arrange_stale_pin()
    return {}


def _circuit_open(ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    _arrange_circuit_open(ctx)
    return {}


def _validator_says_why(_ctx: Any, stack: ExitStack) -> dict[str, Any]:
    stack.enter_context(patch.object(ValidatorPipeline, "execute", return_value=ValidationResult.deny(CANARY)))
    return {}


def _no_budget(_ctx: Any, stack: ExitStack) -> dict[str, Any]:
    stack.enter_context(_budget({"tenant:other": TenantLimits(max_concurrency=5, rps=1, burst=1)}))
    return {}


def _approver_says_why(ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    ctx.approval_gate = _DenyingApprover()
    get_tool_access_resolver().set_mcp_server_policy(_SERVER, ToolAccessPolicy(approval_list=(_TOOL,)))
    return {}


def _start_refused(ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    ctx.get_mcp_server.return_value.state.value = "cold"
    ctx.command_bus.send.side_effect = CannotStartMcpServerError(_SERVER, START_REFUSED_NOT_REVIVED_BY_CALLS)
    return {}


def _pin_unverifiable(ctx: Any, stack: ExitStack) -> dict[str, Any]:
    _arrange_deferred_pin(ctx)
    stack.enter_context(_budget({_TENANT: TenantLimits(max_concurrency=5, rps=100, burst=100)}))
    return {}


def _budget_spent(_ctx: Any, _stack: ExitStack) -> dict[str, Any]:
    return {"global_timeout": 0}


#: Every `_GATES` stage that refuses through `execute`, how to make it refuse,
#: and whether the refusal message holds text from outside the gate (the canary).
#: The message is free text in every case; these are the gates that set it.
_THROUGH_EXECUTE: dict[str, tuple[Callable[[Any, ExitStack], dict[str, Any]], bool]] = {
    "global_timeout": (_budget_spent, False),
    "resolve_target": (_server_missing, False),  # the server name the caller typed
    "tool_access": (_tool_access, False),
    "withdrawal": (_withdrawn, False),  # the tool name
    "digest_pin": (_stale_pin, False),  # the tool name
    "circuit_breaker": (_circuit_open, False),
    "validators": (_validator_says_why, True),  # the validator's reason
    "tenant_budget": (_no_budget, False),
    "approval": (_approver_says_why, True),  # the approver's reason
    "cold_start": (_start_refused, False),
    "deferred_digest_pin": (_pin_unverifiable, False),  # the tool name
}

#: The two cancellation stages, which `execute` reaches only on a race; run on
#: their own, through the same log function the gate loop calls.
_CANCELLATIONS = ("cancelled_before_execution", "cancelled_after_cold_start")


def _refused_lines(captured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in captured if entry["event"] == "batch_call_refused"]


def _assert_no_free_text(line: dict[str, Any], message: str) -> None:
    assert "error" not in line, "the refusal line keeps no free text"
    assert all(message not in str(value) for value in line.values()), line
    assert all(CANARY not in str(value) for value in line.values()), line


def test_every_gate_that_refuses_is_named_here() -> None:
    names = {gate.__name__.removeprefix("_gate_") for gate in _GATES}

    assert set(_THROUGH_EXECUTE) | set(_CANCELLATIONS) == names


class TestARefusalLine:
    @pytest.mark.parametrize("gate", sorted(_THROUGH_EXECUTE))
    def test_carries_no_gate_free_text(self, ctx, gate: str) -> None:
        arrange, planted = _THROUGH_EXECUTE[gate]
        if gate != "deferred_digest_pin":  # that one needs a catalogue not loaded yet
            _arrange_catalogue()
        with ExitStack() as stack:
            run_kwargs = arrange(ctx, stack)
            with structlog.testing.capture_logs() as captured:
                result = _run(**run_kwargs)

        [line] = _refused_lines(captured)
        assert (line["gate"], line["log_level"]) == (gate, "warning")
        assert line["reason"] and line["error_type"] == result.error_type
        _assert_no_free_text(line, result.error)
        # The caller is still told, in the tool result.
        assert result.success is False and result.error
        assert (CANARY in result.error) is planted

    @pytest.mark.parametrize("gate", _CANCELLATIONS)
    def test_of_a_cancellation_carries_no_gate_free_text(self, gate: str) -> None:
        call = CallSpec(index=0, call_id="c-1", mcp_server=_SERVER, tool=_TOOL, arguments={})
        cancelled = Event()
        cancelled.set()

        def refuse(error: str, error_type: str) -> CallResult:
            return CallResult(index=0, call_id="c-1", success=False, error=error, error_type=error_type, elapsed_ms=0)

        p = SimpleNamespace(call=call, cancel_event=cancelled, refuse=refuse, gate_note=None, caller_tenant_id=_TENANT)
        refusal = getattr(BatchExecutor, f"_gate_{gate}")(BatchExecutor(), p)
        with structlog.testing.capture_logs() as captured:
            _log_gate_outcome(p, gate, refusal)  # type: ignore[arg-type]

        [line] = _refused_lines(captured)
        assert (line["gate"], line["reason"], line["error_type"]) == (gate, "cancelled", "CancellationError")
        _assert_no_free_text(line, refusal.error)
        assert refusal.error


class TestAGateThatBroke:
    def test_keeps_its_error_at_debug(self, ctx) -> None:
        """Not a refusal: the machinery failed, and the exception text is what an operator debugs it by.

        The contract forbids upstream text at INFO and above; this line is DEBUG.
        """
        _arrange_catalogue()
        ctx.get_mcp_server.return_value.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError(CANARY)

        with structlog.testing.capture_logs() as captured:
            result = _run()

        assert not _refused_lines(captured)
        [line] = [entry for entry in captured if entry["event"] == "batch_call_failed"]
        assert (line["log_level"], line["reason"]) == ("debug", "start_failed")
        assert CANARY in line["error"] and CANARY in result.error
