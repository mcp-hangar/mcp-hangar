"""Tests for opt-in interceptor registration (validators) — off by default (#314)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from mcp_hangar.application.services.interceptor_registry import (
    BUILTIN_VALIDATORS,
    build_validator_pipeline,
)
from mcp_hangar.domain.contracts.validator import ValidationContext
import mcp_hangar.server.tools.batch as batch


def _ctx(payload: dict) -> ValidationContext:
    return ValidationContext(
        method="tools/call",
        direction="request",
        payload=payload,
        correlation_id="c-1",
    )


_SMALL = {"name": "t", "arguments": {"x": 1}}
_BIG = {"name": "t", "arguments": {"blob": "a" * 500}}


def test_payload_size_spec_denies_oversized_allows_small() -> None:
    pipeline = build_validator_pipeline([{"type": "payload_size", "max_bytes": 50}])

    assert pipeline.execute(_ctx(_BIG)).allowed is False
    assert pipeline.execute(_ctx(_SMALL)).allowed is True


def test_empty_specs_allow_everything() -> None:
    assert build_validator_pipeline([]).execute(_ctx(_BIG)).allowed is True


def test_none_specs_allow_everything() -> None:
    assert build_validator_pipeline(None).execute(_ctx(_BIG)).allowed is True


def test_unknown_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown interceptor validator type"):
        build_validator_pipeline([{"type": "does_not_exist"}])


def test_missing_type_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing required 'type'"):
        build_validator_pipeline([{"max_bytes": 50}])


def test_builtin_validators_contains_payload_size() -> None:
    assert "payload_size" in BUILTIN_VALIDATORS


def test_source_order_preserved() -> None:
    # Two payload_size validators; the stricter one (registered first, same
    # priority) is the effective deny. Order is deterministic (source order).
    pipeline = build_validator_pipeline(
        [
            {"type": "payload_size", "max_bytes": 50, "priority_hint": 10},
            {"type": "payload_size", "max_bytes": 1_000_000, "priority_hint": 20},
        ]
    )
    assert pipeline.execute(_ctx(_BIG)).allowed is False


@pytest.fixture(autouse=True)
def _reset_executor() -> Iterator[None]:
    """Ensure the module global executor is reset to default after each test."""
    yield
    batch.configure_interceptors(None)


def test_configure_interceptors_denies_oversized() -> None:
    batch.configure_interceptors([{"type": "payload_size", "max_bytes": 50}])
    assert batch._executor._validator_pipeline.execute(_ctx(_BIG)).allowed is False
    assert batch._executor._validator_pipeline.execute(_ctx(_SMALL)).allowed is True


def test_configure_interceptors_none_allows_everything() -> None:
    batch.configure_interceptors([{"type": "payload_size", "max_bytes": 50}])
    # Now reset to off — empty pipeline allows everything.
    batch.configure_interceptors(None)
    assert batch._executor._validator_pipeline.execute(_ctx(_BIG)).allowed is True


def test_configure_interceptors_replaces_global_executor() -> None:
    original = batch._executor
    batch.configure_interceptors([{"type": "payload_size", "max_bytes": 50}])
    assert batch._executor is not original
