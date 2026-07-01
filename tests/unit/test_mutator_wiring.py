"""Tests for wiring the interceptor MutatorPipeline into the batch executor (#314).

These exercise ``BatchExecutor._mutate`` directly with an in-memory
``MutatorPipeline`` -- no server, command bus, or concurrency manager required --
proving the transform is behavior-preserving by default (empty pipeline returns
the payload unchanged) and applies registered mutators whose ``applies_to``
includes the method (while skipping those that exclude it).
"""

from mcp_hangar.application.services.mutator_pipeline import MutatorPipeline
from mcp_hangar.domain.contracts.mutator import MutationContext, MutationResult
from mcp_hangar.server.tools.batch.executor import BatchExecutor


class _Mutator:
    """Configurable fake mutator that overwrites the payload when it applies."""

    def __init__(
        self,
        new_payload: dict,
        *,
        applies: tuple[str, ...] = ("tools/call",),
        priority: int = 0,
    ) -> None:
        self._new_payload = new_payload
        self._applies = frozenset(applies)
        self._priority = priority

    @property
    def priority_hint(self) -> int:
        return self._priority

    @property
    def applies_to(self) -> frozenset[str]:
        return self._applies

    def mutate(self, context: MutationContext) -> MutationResult:
        return MutationResult(payload=self._new_payload, changed=True)


def test_default_empty_pipeline_returns_payload_unchanged() -> None:
    # No mutator_pipeline passed -> executor builds a fresh empty pipeline,
    # which is a no-op. This is the behavior-preserving default.
    executor = BatchExecutor()
    payload = {"a": 1}
    out = executor._mutate("tools/call", "request", payload, "corr-1")
    assert out == {"a": 1}
    assert out is payload


def test_explicit_empty_pipeline_returns_payload_unchanged() -> None:
    executor = BatchExecutor(mutator_pipeline=MutatorPipeline())
    out = executor._mutate("tools/call", "response", {"b": 2}, "corr-1")
    assert out == {"b": 2}


def test_applicable_mutator_transforms_payload() -> None:
    pipeline = MutatorPipeline()
    pipeline.register(_Mutator({"mutated": True}))
    executor = BatchExecutor(mutator_pipeline=pipeline)

    out = executor._mutate("tools/call", "request", {"a": 1}, "corr-1")

    assert out == {"mutated": True}


def test_mutator_not_applying_to_method_is_skipped() -> None:
    pipeline = MutatorPipeline()
    # Registered for a different method -> must not touch a tools/call payload.
    pipeline.register(_Mutator({"mutated": True}, applies=("tools/list",)))
    executor = BatchExecutor(mutator_pipeline=pipeline)

    out = executor._mutate("tools/call", "request", {"a": 1}, "corr-1")

    assert out == {"a": 1}
