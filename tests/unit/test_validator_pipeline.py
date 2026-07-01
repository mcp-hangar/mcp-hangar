"""Tests for the IValidator framework: ValidatorPipeline + PayloadSizeValidator (#314)."""

import pytest

from mcp_hangar.application.services.validator_pipeline import ValidatorPipeline
from mcp_hangar.application.validators import PayloadSizeValidator
from mcp_hangar.domain.contracts.validator import ValidationContext, ValidationResult


def _ctx(method: str = "tools/call") -> ValidationContext:
    return ValidationContext(method=method, direction="request", payload={"name": "t"}, correlation_id="c-1")


class _Validator:
    """Configurable fake validator."""

    def __init__(
        self,
        result: ValidationResult | None = None,
        *,
        raises: bool = False,
        fail_open: bool = False,
        priority: int = 0,
        applies: tuple[str, ...] = ("tools/call",),
        record: list[str] | None = None,
    ) -> None:
        self._result = result if result is not None else ValidationResult.allow()
        self._raises = raises
        self._fail_open = fail_open
        self._priority = priority
        self._applies = frozenset(applies)
        self._record = record

    @property
    def priority_hint(self) -> int:
        return self._priority

    @property
    def applies_to(self) -> frozenset[str]:
        return self._applies

    @property
    def fail_open(self) -> bool:
        return self._fail_open

    def validate(self, context: ValidationContext) -> ValidationResult:
        if self._record is not None:
            self._record.append(type(self).__name__ + str(self._priority))
        if self._raises:
            raise RuntimeError("boom")
        return self._result


def test_allows_when_all_validators_allow() -> None:
    p = ValidatorPipeline()
    p.register(_Validator(ValidationResult.allow()))
    assert p.execute(_ctx()).allowed is True


def test_no_applicable_validators_allows() -> None:
    p = ValidatorPipeline()
    p.register(_Validator(applies=("resources/read",)))
    assert p.execute(_ctx("tools/call")).allowed is True


def test_enforced_deny_short_circuits() -> None:
    calls: list[str] = []
    p = ValidatorPipeline()
    p.register(_Validator(ValidationResult.deny("nope"), priority=0, record=calls))
    p.register(_Validator(ValidationResult.allow(), priority=1, record=calls))

    result = p.execute(_ctx())

    assert result.allowed is False
    assert result.reason == "nope"
    assert calls == ["_Validator0"]  # the second validator never ran


def test_audit_only_deny_does_not_enforce() -> None:
    p = ValidatorPipeline()
    p.register(_Validator(ValidationResult.deny("shadow", audit_only=True)))
    assert p.execute(_ctx()).allowed is True


def test_fail_closed_by_default_on_exception() -> None:
    p = ValidatorPipeline()
    p.register(_Validator(raises=True, fail_open=False))

    result = p.execute(_ctx())

    assert result.allowed is False
    assert "failed" in (result.reason or "")


def test_fail_open_opt_in_skips_failing_validator() -> None:
    p = ValidatorPipeline()
    p.register(_Validator(raises=True, fail_open=True))
    assert p.execute(_ctx()).allowed is True


def test_runs_in_priority_order() -> None:
    calls: list[str] = []
    p = ValidatorPipeline()
    p.register(_Validator(priority=10, record=calls))
    p.register(_Validator(priority=1, record=calls))
    p.execute(_ctx())
    assert calls == ["_Validator1", "_Validator10"]


def test_register_rejects_out_of_int32_range() -> None:
    p = ValidatorPipeline()
    with pytest.raises(ValueError):
        p.register(_Validator(priority=2_147_483_648))


def test_payload_size_validator_denies_oversized_and_allows_small() -> None:
    v = PayloadSizeValidator(max_bytes=50)
    small = ValidationContext(method="tools/call", direction="request", payload={"a": 1}, correlation_id="c")
    big = ValidationContext(method="tools/call", direction="request", payload={"a": "x" * 200}, correlation_id="c")

    assert v.validate(small).allowed is True
    denied = v.validate(big)
    assert denied.allowed is False
    assert "exceeds cap" in (denied.reason or "")
    assert v.fail_open is False
    assert v.applies_to == frozenset({"tools/call"})
