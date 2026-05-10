"""Unit tests for IMutator contract, MutationContext, and MutationResult."""

import pytest

from mcp_hangar.domain.contracts.mutator import (
    IMutator,
    MutationContext,
    MutationResult,
    _INT32_MAX,
    _INT32_MIN,
)


class TestMutationContext:
    def test_valid_context(self):
        ctx = MutationContext(
            method="tools/call",
            direction="request",
            payload={"tool": "read"},
            correlation_id="abc-123",
        )
        assert ctx.method == "tools/call"
        assert ctx.direction == "request"
        assert ctx.payload == {"tool": "read"}
        assert ctx.correlation_id == "abc-123"

    def test_frozen(self):
        ctx = MutationContext(
            method="tools/call",
            direction="request",
            payload={},
            correlation_id="x",
        )
        with pytest.raises(AttributeError):
            ctx.method = "other"  # type: ignore[misc]

    def test_rejects_empty_method(self):
        with pytest.raises(ValueError, match="method cannot be empty"):
            MutationContext(method="", direction="request", payload={}, correlation_id="x")

    def test_rejects_empty_correlation_id(self):
        with pytest.raises(ValueError, match="correlation_id cannot be empty"):
            MutationContext(method="tools/call", direction="request", payload={}, correlation_id="")

    def test_response_direction(self):
        ctx = MutationContext(
            method="tools/call",
            direction="response",
            payload={"result": "ok"},
            correlation_id="x",
        )
        assert ctx.direction == "response"


class TestMutationResult:
    def test_unchanged(self):
        r = MutationResult(payload={"x": 1}, changed=False)
        assert not r.changed
        assert r.audit_only is False

    def test_changed(self):
        r = MutationResult(payload={"x": 2}, changed=True)
        assert r.changed

    def test_audit_only_default_false(self):
        r = MutationResult(payload={}, changed=False)
        assert r.audit_only is False

    def test_audit_only_explicit(self):
        r = MutationResult(payload={}, changed=True, audit_only=True)
        assert r.audit_only is True

    def test_frozen(self):
        r = MutationResult(payload={}, changed=False)
        with pytest.raises(AttributeError):
            r.changed = True  # type: ignore[misc]


class _StubMutator:
    """Concrete mutator for Protocol conformance testing."""

    def __init__(self, hint: int = 0, methods: frozenset[str] | None = None) -> None:
        self._hint = hint
        self._methods = methods or frozenset({"tools/call"})

    @property
    def priority_hint(self) -> int:
        return self._hint

    @property
    def applies_to(self) -> frozenset[str]:
        return self._methods

    def mutate(self, context: MutationContext) -> MutationResult:
        return MutationResult(payload=context.payload, changed=False)


class TestIMutatorProtocol:
    def test_stub_is_imutator(self):
        assert isinstance(_StubMutator(), IMutator)

    def test_int32_range_constants(self):
        assert _INT32_MIN == -2_147_483_648
        assert _INT32_MAX == 2_147_483_647

    def test_boundary_priority_hints(self):
        low = _StubMutator(hint=_INT32_MIN)
        high = _StubMutator(hint=_INT32_MAX)
        assert low.priority_hint == _INT32_MIN
        assert high.priority_hint == _INT32_MAX
