"""MutatorPipeline: sequential priority-based mutator execution.

Sorts registered IMutator instances by (priority_hint, registration_index)
and applies them sequentially to a MutationContext.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_hangar.domain.contracts.mutator import _INT32_MAX, _INT32_MIN, MutationContext, MutationResult
from mcp_hangar.logging_config import get_logger

if TYPE_CHECKING:
    from mcp_hangar.domain.contracts.mutator import IMutator

logger = get_logger(__name__)


class MutatorPipeline:
    """Applies registered mutators in priority order.

    Mutators are sorted by (priority_hint ascending, registration_index ascending).
    Only mutators whose ``applies_to`` includes the context's method are invoked.
    """

    def __init__(self) -> None:
        self._mutators: list[tuple[int, IMutator]] = []
        self._sorted: list[IMutator] = []
        self._dirty = False

    def register(self, mutator: IMutator) -> None:
        """Add a mutator to the pipeline.

        Raises:
            ValueError: If priority_hint is outside int32 range.
        """
        hint = mutator.priority_hint
        if hint < _INT32_MIN or hint > _INT32_MAX:
            raise ValueError(f"priority_hint {hint} outside int32 range [{_INT32_MIN}, {_INT32_MAX}]")
        idx = len(self._mutators)
        self._mutators.append((idx, mutator))
        self._dirty = True
        logger.debug(
            "mutator_registered",
            mutator=type(mutator).__name__,
            priority_hint=hint,
            index=idx,
        )

    def execute(self, context: MutationContext) -> MutationResult:
        """Run all applicable mutators sequentially.

        Returns identity (unchanged payload) if no mutators apply.
        """
        if self._dirty:
            self._sorted = [m for _, m in sorted(self._mutators, key=lambda pair: (pair[1].priority_hint, pair[0]))]
            self._dirty = False

        current_payload = context.payload
        any_changed = False

        for mutator in self._sorted:
            if context.method not in mutator.applies_to:
                continue
            step_ctx = MutationContext(
                method=context.method,
                direction=context.direction,
                payload=current_payload,
                correlation_id=context.correlation_id,
            )
            result = mutator.mutate(step_ctx)
            if result.changed:
                current_payload = result.payload
                any_changed = True

        return MutationResult(payload=current_payload, changed=any_changed)
