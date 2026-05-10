"""Unit tests for MutatorPipeline ordering and execution."""

import pytest

from mcp_hangar.application.services.mutator_pipeline import MutatorPipeline
from mcp_hangar.domain.contracts.mutator import (
    MutationContext,
    MutationResult,
    _INT32_MAX,
    _INT32_MIN,
)


def _ctx(method: str = "tools/call", direction: str = "response") -> MutationContext:
    return MutationContext(
        method=method,
        direction=direction,
        payload={"result": "original"},
        correlation_id="test-corr",
    )


class _AppendMutator:
    """Appends a tag to payload['result'] to trace execution order."""

    def __init__(self, tag: str, hint: int = 0, methods: frozenset[str] | None = None) -> None:
        self._tag = tag
        self._hint = hint
        self._methods = methods or frozenset({"tools/call"})

    @property
    def priority_hint(self) -> int:
        return self._hint

    @property
    def applies_to(self) -> frozenset[str]:
        return self._methods

    def mutate(self, context: MutationContext) -> MutationResult:
        new_payload = {**context.payload, "result": context.payload.get("result", "") + f":{self._tag}"}
        return MutationResult(payload=new_payload, changed=True)


class _NoopMutator:
    def __init__(self, hint: int = 0) -> None:
        self._hint = hint

    @property
    def priority_hint(self) -> int:
        return self._hint

    @property
    def applies_to(self) -> frozenset[str]:
        return frozenset({"tools/call"})

    def mutate(self, context: MutationContext) -> MutationResult:
        return MutationResult(payload=context.payload, changed=False)


class TestMutatorPipeline:
    def test_empty_pipeline_returns_identity(self):
        pipeline = MutatorPipeline()
        result = pipeline.execute(_ctx())
        assert result.payload == {"result": "original"}
        assert result.changed is False

    def test_single_mutator(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("A"))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:A"
        assert result.changed is True

    def test_priority_ordering_ascending(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("C", hint=30))
        pipeline.register(_AppendMutator("A", hint=10))
        pipeline.register(_AppendMutator("B", hint=20))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:A:B:C"

    def test_tie_broken_by_registration_order(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("first", hint=0))
        pipeline.register(_AppendMutator("second", hint=0))
        pipeline.register(_AppendMutator("third", hint=0))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:first:second:third"

    def test_applies_to_filters_methods(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("X", methods=frozenset({"tools/list"})))
        result = pipeline.execute(_ctx(method="tools/call"))
        assert result.payload["result"] == "original"
        assert result.changed is False

    def test_noop_mutator_does_not_set_changed(self):
        pipeline = MutatorPipeline()
        pipeline.register(_NoopMutator())
        result = pipeline.execute(_ctx())
        assert result.changed is False

    def test_mixed_changed_and_unchanged(self):
        pipeline = MutatorPipeline()
        pipeline.register(_NoopMutator(hint=0))
        pipeline.register(_AppendMutator("A", hint=10))
        pipeline.register(_NoopMutator(hint=20))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:A"
        assert result.changed is True

    def test_int32_min_priority(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("min", hint=_INT32_MIN))
        pipeline.register(_AppendMutator("zero", hint=0))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:min:zero"

    def test_int32_max_priority(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("max", hint=_INT32_MAX))
        pipeline.register(_AppendMutator("zero", hint=0))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:zero:max"

    def test_out_of_range_priority_raises(self):
        pipeline = MutatorPipeline()
        m = _AppendMutator("bad", hint=_INT32_MAX + 1)
        with pytest.raises(ValueError, match="outside int32 range"):
            pipeline.register(m)

    def test_out_of_range_negative_priority_raises(self):
        pipeline = MutatorPipeline()
        m = _AppendMutator("bad", hint=_INT32_MIN - 1)
        with pytest.raises(ValueError, match="outside int32 range"):
            pipeline.register(m)

    def test_multiple_methods_same_mutator(self):
        pipeline = MutatorPipeline()
        pipeline.register(_AppendMutator("multi", methods=frozenset({"tools/call", "tools/list"})))

        r1 = pipeline.execute(_ctx(method="tools/call"))
        assert r1.payload["result"] == "original:multi"

        r2 = pipeline.execute(_ctx(method="tools/list"))
        assert r2.payload["result"] == "original:multi"

    @pytest.mark.parametrize(
        "hints",
        [
            [3, 1, 2],
            [2, 3, 1],
            [1, 2, 3],
            [3, 2, 1],
        ],
    )
    def test_order_invariant_to_registration_permutation(self, hints: list[int]):
        pipeline = MutatorPipeline()
        for h in hints:
            pipeline.register(_AppendMutator(str(h), hint=h))
        result = pipeline.execute(_ctx())
        assert result.payload["result"] == "original:1:2:3"
