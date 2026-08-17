"""An L7 requireApproval verdict asks a human instead of just failing closed.

Regression for #921 -- the one MCPEgressPolicy acceptance criterion that was
never met. The policy author writes three outcomes (allow / requireApproval /
deny) and got two: in Enforce, requireApproval raised
EgressPolicyApprovalRequiredError unconditionally, indistinguishable from
deny except by the error string.

Wired now through the machinery that already exists and is already governed:
the executor's approval gate consults the target's L7 policy alongside the
MRTR ToolAccessPolicy, blocks on ApprovalGateService.check, and a granted
(and dispatch-revalidated) approval id rides the InvokeToolCommand into the
aggregate, where it converts exactly the require-approval verdict -- deny
still wins, Audit never asks, and no configured gate stays fail-closed.
"""

from types import SimpleNamespace

from mcp_hangar.approvals.models import ApprovalResult
from mcp_hangar.domain.exceptions import (
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
)
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.server.tools.batch.executor import BatchExecutor, _approval_loop_local

_POLICY = L7Policy.from_dict(
    {
        "tools": {"deny": ["boom"], "requireApproval": ["store_*"]},
        "defaultAction": "Allow",
        "mode": "Enforce",
    }
)


def _server(policy: L7Policy | None = _POLICY) -> McpServer:
    server = McpServer(mcp_server_id="egress-demo", mode="remote", endpoint="https://up.example/mcp")
    server.set_l7_policy(policy)
    return server


class TestTheAggregateHonorsAGrantedApproval:
    def test_without_an_approval_the_verdict_fails_closed(self):
        try:
            _server().invoke_tool("store_secret", {"v": "x"})
            raise AssertionError("require_approval did not block")
        except EgressPolicyApprovalRequiredError:
            pass

    def test_a_granted_approval_converts_the_verdict(self):
        # The call proceeds past the L7 gate; whatever fails later (this test
        # has no live upstream) must not be the approval error.
        try:
            _server().invoke_tool("store_secret", {"v": "x"}, l7_approval_id="appr-1")
        except EgressPolicyApprovalRequiredError:
            raise AssertionError("granted approval was ignored") from None
        except Exception:  # noqa: BLE001 -- anything downstream of the gate is fine here
            pass

    def test_an_approval_does_not_override_deny(self):
        # The id converts require-approval only: a policy hardened to deny
        # during the hold still refuses (the #673 revalidation shape).
        try:
            _server().invoke_tool("boom", {}, l7_approval_id="appr-1")
            raise AssertionError("deny was overridden by an approval id")
        except EgressPolicyDeniedError:
            pass


class _Ctx(SimpleNamespace):
    pass


def _executor() -> BatchExecutor:
    return object.__new__(BatchExecutor)


def _unrestricted_resolver():
    policy = SimpleNamespace(is_unrestricted=lambda: True, requires_approval=lambda tool: False)
    return SimpleNamespace(resolve_effective_policy=lambda _key: policy)


def _call(tool: str):
    return SimpleNamespace(mcp_server="egress-demo", tool=tool, arguments={}, call_id="c1", index=0)


class _Repo:
    def __init__(self, server):
        self._server = server

    def get(self, _id):
        return self._server


class _Gate:
    """Records the policy it was asked with and answers a fixed result."""

    def __init__(self, result: ApprovalResult):
        self._result = result
        self.asked_with = None

    async def check(self, **kwargs):
        self.asked_with = kwargs
        return self._result


class TestTheExecutorRoutesL7ToTheGate:
    def test_l7_only_call_is_gated_with_a_policy_naming_the_tool(self):
        gate = _Gate(ApprovalResult.granted("appr-42"))
        ctx = _Ctx(repository=_Repo(_server()), approval_gate=gate)

        result = _executor()._check_approval_gate(_call("store_secret"), _unrestricted_resolver(), ctx)

        assert result is None  # approved: continue execution
        assert gate.asked_with is not None, "the gate was never consulted for an L7 requireApproval"
        assert gate.asked_with["policy"].requires_approval("store_secret")
        assert _approval_loop_local.approval_id == "appr-42"

    def test_a_refusal_from_the_gate_blocks_the_call(self):
        gate = _Gate(ApprovalResult.denied("appr-43", "no"))
        ctx = _Ctx(repository=_Repo(_server()), approval_gate=gate)

        result = _executor()._check_approval_gate(_call("store_secret"), _unrestricted_resolver(), ctx)

        assert result is not None and result.success is False

    def test_no_gate_configured_stays_fail_closed(self):
        # The aggregate raises on invoke, as before this wiring; the gate
        # check must not fabricate a pass.
        ctx = _Ctx(repository=_Repo(_server()), approval_gate=None)

        result = _executor()._check_approval_gate(_call("store_secret"), _unrestricted_resolver(), ctx)

        assert result is None
        assert getattr(_approval_loop_local, "approval_id", None) is None

    def test_allowed_and_denied_tools_do_not_ask_a_human(self):
        gate = _Gate(ApprovalResult.granted("appr-44"))
        ctx = _Ctx(repository=_Repo(_server()), approval_gate=gate)

        assert _executor()._check_approval_gate(_call("fetch"), _unrestricted_resolver(), ctx) is None
        assert _executor()._check_approval_gate(_call("boom"), _unrestricted_resolver(), ctx) is None
        assert gate.asked_with is None

    def test_audit_mode_never_asks(self):
        audit = L7Policy.from_dict(
            {"tools": {"requireApproval": ["store_*"]}, "defaultAction": "Allow", "mode": "Audit"}
        )
        gate = _Gate(ApprovalResult.granted("appr-45"))
        ctx = _Ctx(repository=_Repo(_server(audit)), approval_gate=gate)

        assert _executor()._check_approval_gate(_call("store_secret"), _unrestricted_resolver(), ctx) is None
        assert gate.asked_with is None
