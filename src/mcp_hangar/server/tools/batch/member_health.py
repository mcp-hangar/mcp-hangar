"""What one call's outcome says about the group member that took it (#1409).

A group takes a member out of rotation, and opens its circuit, on the failures
reported to it. So only an outcome that is evidence the member is unwell may be
reported as one. Before #1409 every call that did not succeed was: a caller
dividing by zero, or sending an argument the tool rejects, took a healthy member
out, and enough of them opened the circuit. The member was fine. It answered
that the request was wrong.

`member_outcome` gives one of three verdicts:

- ``HEALTHY``: the member answered. A tool-level error (``isError: true``), or a
  JSON-RPC error that answers the request itself, is a working exchange about a
  bad request. The group hears ``report_success``.
- ``UNHEALTHY``: evidence against the member. The transport failed, the call
  timed out, the process is gone, the member failed to start or is in its own
  backoff, or it answered with a protocol-level error. The group hears
  ``report_failure``.
- ``UNJUDGED``: Hangar decided before the member was asked: its policy, pins,
  validators, approval, rate limit or the tenant's budget, or the caller's own
  cancel or deadline. The group hears nothing.

The executor asks this module on both of its reporting paths: a gate's refusal
of the selected member, and the invocation's outcome. The other reporter,
``GroupRebalanceSaga``, reports only events that are the member's own health (a
start, a health check, a start failure that degraded the server), so it needs
no verdict.
"""

from __future__ import annotations

from enum import Enum

from ...._sdk_compat import INVALID_PARAMS, METHOD_NOT_FOUND
from ....domain.exceptions import (
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    RateLimitExceeded,
    ToolAccessDeniedError,
    ToolInvocationError,
    ToolNotFoundError,
)


class MemberOutcome(Enum):
    """What an outcome tells the group about the member that took the call."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNJUDGED = "unjudged"


#: Refusal codes the executor writes that report the selected member's own state:
#: its per-server circuit is open, it is DEAD in its backoff or for a capability
#: block, or its start failed (#1361, #1446).
MEMBER_HEALTH_REFUSALS = frozenset({"CircuitBreakerOpen", "CannotStartMcpServerError", "McpServerStartError"})

#: Refusal codes the executor writes that say nothing about the member. Every code
#: the executor writes is in exactly one of these two sets, so a new one has to be
#: sorted on purpose (tests/unit/test_a_group_member_is_judged_by_its_own_health.py).
NOT_MEMBER_REFUSALS = frozenset(
    {
        # Hangar's gates: policy, withdrawal, pins, validators, approval, budget.
        "ToolAccessDeniedError",
        "ToolAccessDenied",
        "ToolWithdrawnError",
        "ToolDigestMismatchError",
        "ValidatorDenied",
        "ApprovalDenied",
        "approval_denied",
        "approval_timeout",
        "ApprovalGateError",
        "ApprovalNoLongerValid",
        "ApprovalRevalidationError",
        "TenantQuotaExceeded",
        # The caller's own batch: cancelled, or past its deadline before the call ran.
        "CancellationError",
        "TimeoutError",
        # No member was selected, or the name is not a server.
        "NoAvailableMemberError",
        "McpServerNotFoundError",
        # The member answered with a task handle, and task relay is off.
        "TaskRelayNotSupported",
    }
)

#: Raised on the invoke path by Hangar itself, before the member is asked: the L7
#: egress policy, the command bus's rate limit, and a tool the member's catalogue
#: does not have.
_HANGAR_REFUSALS: tuple[type[Exception], ...] = (
    ToolAccessDeniedError,
    EgressPolicyDeniedError,
    EgressPolicyApprovalRequiredError,
    RateLimitExceeded,
    ToolNotFoundError,
)

#: JSON-RPC 2.0 reserves -32768 to -32000 for the protocol and the server. A code
#: outside it is the tool's own error for this request.
_RESERVED_LOW, _RESERVED_HIGH = -32768, -32000


def _answers_the_request(code: object) -> bool:
    """Whether a JSON-RPC error with *code* is the member's answer to the request.

    Invalid params and method not found answer the request. So does any code
    outside the reserved range, such as ``-1`` for a division by zero. Every
    other reserved code, a parse error, an invalid request, an internal error or
    a server error, says the exchange failed. So does an error without an
    integer code.
    """
    if type(code) is not int:
        return False
    return code in (INVALID_PARAMS, METHOD_NOT_FOUND) or not (_RESERVED_LOW <= code <= _RESERVED_HIGH)


def member_outcome(cause: str | BaseException | None) -> MemberOutcome:
    """What *cause* says about the group member that took the call.

    *cause* is one of these:

    - a refusal code the executor wrote. Only the codes in
      `MEMBER_HEALTH_REFUSALS` count. Any other code is a gate deciding, not
      the member failing.
    - the exception a failed invocation raised. A tool-level error, or a
      JSON-RPC error that answers the request, is ``HEALTHY``. A refusal
      Hangar raised before asking the member is ``UNJUDGED``. Anything else
      counts, as it did before #1409: the transport, a timeout, a start
      failure, a protocol error, or an error nobody classified.
    - None, for a failed invocation that recorded no error. It counts.
    """
    if isinstance(cause, str):
        return MemberOutcome.UNHEALTHY if cause in MEMBER_HEALTH_REFUSALS else MemberOutcome.UNJUDGED
    if isinstance(cause, _HANGAR_REFUSALS):
        return MemberOutcome.UNJUDGED
    if isinstance(cause, ToolInvocationError):
        if cause.details.get("is_error"):
            return MemberOutcome.HEALTHY
        if "jsonrpc_code" in cause.details and _answers_the_request(cause.details["jsonrpc_code"]):
            return MemberOutcome.HEALTHY
    return MemberOutcome.UNHEALTHY
