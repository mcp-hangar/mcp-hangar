"""ValidatorPipeline: sequential priority-based validator execution (PR #2624).

Companion to :class:`~mcp_hangar.application.services.mutator_pipeline.MutatorPipeline`.
Sorts registered :class:`IValidator` instances by (priority_hint, registration_index)
and runs them until one denies (enforced) or all allow.

**Fail-closed by default:** if a validator raises, the pipeline denies the payload
unless that validator declares ``fail_open`` — the normative default from PR #2624.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp_hangar.domain.contracts.validator import (
    _INT32_MAX,
    _INT32_MIN,
    ValidationContext,
    ValidationResult,
)
from mcp_hangar.logging_config import get_logger

if TYPE_CHECKING:
    from mcp_hangar.domain.contracts.validator import IValidator

logger = get_logger(__name__)


class ValidatorPipeline:
    """Runs registered validators in priority order, fail-closed.

    Validators are sorted by (priority_hint ascending, registration_index ascending).
    Only validators whose ``applies_to`` includes the context's method run. The first
    enforced denial short-circuits; ``audit_only`` denials are not enforced.
    """

    def __init__(self) -> None:
        self._validators: list[tuple[int, IValidator]] = []
        self._sorted: list[IValidator] = []
        self._dirty = False

    def register(self, validator: IValidator) -> None:
        """Add a validator to the pipeline.

        Raises:
            ValueError: If priority_hint is outside int32 range.
        """
        hint = validator.priority_hint
        if hint < _INT32_MIN or hint > _INT32_MAX:
            raise ValueError(f"priority_hint {hint} outside int32 range [{_INT32_MIN}, {_INT32_MAX}]")
        idx = len(self._validators)
        self._validators.append((idx, validator))
        self._dirty = True
        logger.debug(
            "validator_registered",
            validator=type(validator).__name__,
            priority_hint=hint,
            index=idx,
            fail_open=validator.fail_open,
        )

    def execute(self, context: ValidationContext) -> ValidationResult:
        """Run all applicable validators; allow only if none enforce a denial.

        Returns allow if no validators apply.
        """
        if self._dirty:
            self._sorted = [v for _, v in sorted(self._validators, key=lambda pair: (pair[1].priority_hint, pair[0]))]
            self._dirty = False

        for validator in self._sorted:
            if context.method not in validator.applies_to:
                continue
            try:
                result = validator.validate(context)
            except Exception as exc:  # noqa: BLE001 -- fault barrier; default is fail-closed per PR #2624
                name = type(validator).__name__
                if validator.fail_open:
                    logger.warning("validator_error_fail_open", validator=name, error=str(exc))
                    continue
                logger.error("validator_error_fail_closed", validator=name, error=str(exc))
                return ValidationResult.deny(f"validator {name} failed: {exc}")

            if not result.allowed and not result.audit_only:
                logger.info(
                    "validator_denied",
                    validator=type(validator).__name__,
                    reason=result.reason,
                    correlation_id=context.correlation_id,
                )
                return result

        return ValidationResult.allow()
