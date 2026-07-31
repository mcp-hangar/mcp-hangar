"""Tests for wiring the interceptor ValidatorPipeline into the batch executor (#314).

These exercise ``BatchExecutor._check_validators`` directly with an in-memory
``ValidatorPipeline`` -- no server, command bus, or concurrency manager required --
proving the gate is behavior-preserving by default (empty pipeline allows) and
fail-closed once a deny-validator is registered.
"""

from mcp_hangar.application.services.validator_pipeline import ValidatorPipeline
from mcp_hangar.domain.contracts.validator import ValidationContext, ValidationResult
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallSpec


class _Validator:
    """Configurable fake validator gating ``tools/call`` requests."""

    def __init__(
        self,
        result: ValidationResult,
        *,
        applies: tuple[str, ...] = ("tools/call",),
    ) -> None:
        self._result = result
        self._applies = frozenset(applies)

    @property
    def priority_hint(self) -> int:
        return 0

    @property
    def applies_to(self) -> frozenset[str]:
        return self._applies

    @property
    def fail_open(self) -> bool:
        return False

    def validate(self, context: ValidationContext) -> ValidationResult:
        return self._result


def _call() -> CallSpec:
    return CallSpec(
        index=0,
        call_id="call-1",
        mcp_server="srv",
        tool="do_thing",
        arguments={"a": 1},
    )


def test_default_empty_pipeline_proceeds() -> None:
    # No validator_pipeline passed -> executor builds a fresh empty pipeline,
    # which always allows. This is the behavior-preserving default.
    executor = BatchExecutor()
    assert executor._check_validators(_call()) is None


def test_explicit_empty_pipeline_proceeds() -> None:
    executor = BatchExecutor(validator_pipeline=ValidatorPipeline())
    assert executor._check_validators(_call()) is None


def test_allow_validator_proceeds() -> None:
    pipeline = ValidatorPipeline()
    pipeline.register(_Validator(ValidationResult.allow()))
    executor = BatchExecutor(validator_pipeline=pipeline)
    assert executor._check_validators(_call()) is None


def test_deny_validator_short_circuits_fail_closed() -> None:
    pipeline = ValidatorPipeline()
    pipeline.register(_Validator(ValidationResult.deny("nope")))
    executor = BatchExecutor(validator_pipeline=pipeline)

    result = executor._check_validators(_call())

    assert result is not None
    assert result.success is False
    assert result.error_type == "ValidatorDenied"
    assert result.error == "nope"
    assert result.call_id == "call-1"
    assert result.index == 0


def test_deny_validator_without_reason_uses_default_message() -> None:
    pipeline = ValidatorPipeline()
    # ValidationResult built directly with no reason -> executor supplies a default.
    pipeline.register(_Validator(ValidationResult(allowed=False)))
    executor = BatchExecutor(validator_pipeline=pipeline)

    result = executor._check_validators(_call())

    assert result is not None
    assert result.success is False
    assert result.error_type == "ValidatorDenied"
    assert result.error == "Denied by validator"
