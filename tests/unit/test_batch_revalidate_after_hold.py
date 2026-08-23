"""Every branch of the post-approval-hold re-check.

``BatchExecutor._revalidate_after_hold`` is the gate that closes the TOCTOU
window an approval hold opens: the call was authorized against a world that
existed before a human was asked, and the hold can last five minutes or more.
It re-checks three things -- the approval record, the effective tool-access
policy, and the pinned tool digest -- and every one of them must fail closed.

A branch-coverage measurement of the decision-path modules put this method at
**0.00%**, with ``grep -rn _revalidate_after_hold tests/`` returning nothing.
The confusion worth naming: ``ApprovalGateService.revalidate`` *is* tested
(tests/unit/components/approvals/test_approval_revalidation.py), and it is a
different function. That one re-checks the approval record; this one wraps it
and adds the policy and digest re-checks, and decides what the batch does with
each answer.

So these tests are written per branch, and each asserts the error_type as well
as the refusal -- a refusal carrying the wrong code sends the caller down the
wrong retry path.
"""

from unittest.mock import Mock

import pytest

from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallResult, CallSpec


def _call(**overrides) -> CallSpec:
    data = {
        "index": 0,
        "call_id": "c-1",
        "mcp_server": "payments",
        "tool": "transfer",
        "arguments": {"amount": 10},
    }
    data.update(overrides)
    return CallSpec(**data)


def _resolver(*, allowed=True, unrestricted=False, raises=None):
    """A tool-access resolver whose effective policy is under test control."""
    resolver = Mock()
    if raises is not None:
        resolver.resolve_effective_policy.side_effect = raises
        return resolver
    policy = Mock()
    policy.is_unrestricted.return_value = unrestricted
    policy.is_tool_allowed.return_value = allowed
    resolver.resolve_effective_policy.return_value = policy
    return resolver


def _ctx(*, revalidate_returns=None, revalidate_raises=None, with_gate=True):
    """An application context whose approval gate answers as the test wants."""
    if not with_gate:
        ctx = Mock()
        ctx.approval_gate = None
        return ctx

    async def revalidate(_approval_id, _arguments):
        if revalidate_raises is not None:
            raise revalidate_raises
        return revalidate_returns

    gate = Mock()
    gate.revalidate = revalidate
    ctx = Mock()
    ctx.approval_gate = gate
    return ctx


def _revalidate(executor, *, call=None, resolver=None, ctx=None, pin=None, projection="unset", enforce=None):
    proj_registry = Mock()
    proj_registry.resolve.return_value = None if projection == "unset" else projection
    return executor._revalidate_after_hold(
        call=call or _call(),
        resolver=resolver if resolver is not None else _resolver(),
        ctx=ctx if ctx is not None else _ctx(),
        approval_id="ap-1",
        pin=pin,
        proj_registry=proj_registry,
        caller_tenant_id=None,
        enforce_digest_pin=enforce or (lambda _projection, _pin: None),
    )


@pytest.fixture
def executor():
    return BatchExecutor()


class TestApprovalRecordRecheck:
    def test_still_valid_approval_proceeds(self, executor):
        assert _revalidate(executor, ctx=_ctx(revalidate_returns=None)) is None

    def test_reason_from_the_gate_refuses(self, executor):
        result = _revalidate(executor, ctx=_ctx(revalidate_returns="approval expired during the hold"))

        assert isinstance(result, CallResult)
        assert result.success is False
        assert result.error_type == "ApprovalNoLongerValid"
        assert "expired during the hold" in result.error

    @pytest.mark.parametrize(
        "exc",
        [RuntimeError("loop gone"), OSError("disk"), ValueError("bad record"), TimeoutError("slow")],
    )
    def test_revalidation_error_fails_closed(self, executor, exc):
        """An approval we cannot re-verify is not an approval we can act on."""
        result = _revalidate(executor, ctx=_ctx(revalidate_raises=exc))

        assert result is not None
        assert result.error_type == "ApprovalRevalidationError"
        assert "revalidation error" in result.error

    def test_absent_gate_service_skips_the_record_check(self, executor):
        """Not every entry point wires an approval gate; that is not a refusal."""
        assert _revalidate(executor, ctx=_ctx(with_gate=False)) is None

    def test_gate_without_revalidate_is_skipped(self, executor):
        """An older gate object without the method must not crash the dispatch."""
        gate = object()  # no .revalidate attribute
        ctx = Mock()
        ctx.approval_gate = gate

        assert _revalidate(executor, ctx=ctx) is None


class TestPolicyRecheck:
    def test_tool_denied_during_the_hold_is_refused(self, executor):
        result = _revalidate(executor, resolver=_resolver(allowed=False))

        assert result is not None
        assert result.error_type == "ToolAccessDenied"
        assert "no longer allowed by policy" in result.error

    def test_fully_unrestricted_proceeds(self, executor):
        resolver = Mock()
        policy = Mock()
        policy.is_unrestricted.return_value = True
        resolver.resolve_effective_policy.return_value = policy

        assert _revalidate(executor, resolver=resolver) is None

    def test_unreadable_policy_fails_closed(self, executor):
        result = _revalidate(executor, resolver=_resolver(raises=RuntimeError("store down")))

        assert result is not None
        assert result.error_type == "ApprovalRevalidationError"
        assert "policy could not be re-resolved" in result.error


class TestDigestPinRecheck:
    def test_no_pin_skips_the_digest_check(self, executor):
        enforce = Mock()
        assert _revalidate(executor, pin=None, enforce=enforce) is None
        enforce.assert_not_called()

    def test_pin_without_a_projection_skips(self, executor):
        """Nothing in the catalogue to compare against is not a refusal here."""
        enforce = Mock()
        assert _revalidate(executor, pin=Mock(), projection=None, enforce=enforce) is None
        enforce.assert_not_called()

    def test_digest_drift_during_the_hold_is_refused(self, executor):
        """The pre-gate check spoke for a schema that may since have moved."""
        rejection = CallResult(
            index=0,
            call_id="c-1",
            success=False,
            error="digest mismatch",
            error_type="DigestMismatch",
            elapsed_ms=0,
        )

        result = _revalidate(
            executor,
            pin=Mock(),
            projection=Mock(),
            enforce=lambda _projection, _pin: rejection,
        )

        assert result is rejection

    def test_matching_digest_proceeds(self, executor):
        assert _revalidate(executor, pin=Mock(), projection=Mock(), enforce=lambda _p, _pin: None) is None


class TestRefusalShape:
    """A refusal must be answerable by the caller, not just non-None."""

    def test_refusal_carries_the_call_identity(self, executor):
        result = _revalidate(
            executor,
            call=_call(index=7, call_id="c-7"),
            resolver=_resolver(allowed=False),
        )

        assert result.index == 7
        assert result.call_id == "c-7"
        assert result.elapsed_ms == 0
        assert result.error.startswith("Approval no longer valid at dispatch:")
