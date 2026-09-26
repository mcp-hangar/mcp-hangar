"""The approval gate's warnings carry an exception's bounded type, never its text (#1590).

`approval_gate_error` (creating the approval request raised) and
`approval_revalidation_failed` (re-checking it after the hold raised) logged
`str(exc)` at WARNING. An internal exception's message can carry anything its
source put in it: an approval store's connection string, a policy engine's echo
of the arguments. The data-handling contract allows a log line at INFO and
above event type, identifiers and bounded codes, with full detail at DEBUG
(#1276, R7).

The caller is still told, in the tool result. Only the log changes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import structlog

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallSpec

#: Planted in the raised exception's message.
CANARY = "canary-5d21-store://approver:s3cret@store.internal/approvals"

_SERVER = "payments"
_TOOL = "transfer"

_RAISED = [RuntimeError, OSError, ValueError, TimeoutError]


def _call() -> CallSpec:
    return CallSpec(index=0, call_id="c-1", mcp_server=_SERVER, tool=_TOOL, arguments={"amount": 10})


def _lines(captured: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [entry for entry in captured if entry["event"] == event]


def _assert_bounded(warning: dict[str, Any], exc_type: type[BaseException]) -> None:
    assert warning["log_level"] == "warning"
    assert warning["error_type"] == exc_type.__name__
    assert all(CANARY not in str(value) for value in warning.values()), warning


def _assert_detail_at_debug(captured: list[dict[str, Any]]) -> None:
    detail = [entry for entry in captured if entry["log_level"] == "debug" and CANARY in str(entry)]
    assert detail, "the full exception text is kept at DEBUG"


class _RaisingGate:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def check(self, **_kwargs: Any) -> Any:
        raise self._exc

    async def revalidate(self, _approval_id: str, _arguments: Any) -> Any:
        raise self._exc


@pytest.mark.parametrize("exc_type", _RAISED)
def test_approval_gate_error_warns_with_the_bounded_type(exc_type: type[BaseException]) -> None:
    ctx = SimpleNamespace(approval_gate=_RaisingGate(exc_type(CANARY)))
    governance = SimpleNamespace(approval_policy=ToolAccessPolicy(approval_list=(_TOOL,)), l7_policy=None)

    with structlog.testing.capture_logs() as captured:
        result = BatchExecutor()._check_approval_gate(_call(), Mock(), ctx, governance=governance)  # type: ignore[arg-type]

    [warning] = _lines(captured, "approval_gate_error")
    _assert_bounded(warning, exc_type)
    assert warning["tool"] == _TOOL and warning["call_id"] == "c-1"
    _assert_detail_at_debug(captured)
    # The caller-facing result is unchanged.
    assert result is not None and result.success is False
    assert result.error_type == "ApprovalGateError"
    assert result.error == f"Approval gate error: {CANARY}"


@pytest.mark.parametrize("exc_type", _RAISED)
def test_approval_revalidation_failed_warns_with_the_bounded_type(exc_type: type[BaseException]) -> None:
    ctx = SimpleNamespace(approval_gate=_RaisingGate(exc_type(CANARY)))
    proj_registry = Mock()
    proj_registry.resolve.return_value = None

    with structlog.testing.capture_logs() as captured:
        result = BatchExecutor()._revalidate_after_hold(
            call=_call(),
            resolver=Mock(),
            ctx=ctx,
            approval_id="ap-1",
            pin=None,
            proj_registry=proj_registry,
            caller_tenant_id=None,
            enforce_digest_pin=lambda _projection, _pin: None,
        )

    [warning] = _lines(captured, "approval_revalidation_failed")
    _assert_bounded(warning, exc_type)
    assert (warning["approval_id"], warning["reason"]) == ("ap-1", "revalidation error")
    _assert_detail_at_debug(captured)
    # The caller-facing result is unchanged.
    assert result is not None and result.success is False
    assert result.error_type == "ApprovalRevalidationError"
    assert result.error == f"Approval no longer valid at dispatch: revalidation error: {CANARY}"


def test_a_fixed_reason_is_logged_as_it_was() -> None:
    """A reason the gateway wrote itself is not exception text and stays on the warning."""

    class _Expired:
        async def revalidate(self, _approval_id: str, _arguments: Any) -> str:
            return "approval expired during the hold"

    proj_registry = Mock()
    proj_registry.resolve.return_value = None
    with structlog.testing.capture_logs() as captured:
        BatchExecutor()._revalidate_after_hold(
            call=_call(),
            resolver=Mock(),
            ctx=SimpleNamespace(approval_gate=_Expired()),
            approval_id="ap-1",
            pin=None,
            proj_registry=proj_registry,
            caller_tenant_id=None,
            enforce_digest_pin=lambda _projection, _pin: None,
        )

    [warning] = _lines(captured, "approval_revalidation_failed")
    assert warning["reason"] == "approval expired during the hold"
    assert warning["error_type"] is None
