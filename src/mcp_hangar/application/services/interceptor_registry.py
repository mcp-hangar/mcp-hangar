"""Config-driven, opt-in registration of built-in interceptors (validators).

Operators enable specific built-in validators via an ``interceptors:`` config
section (see ``config.yaml.example``). This is **off by default**: an empty or
absent spec list produces an empty :class:`ValidatorPipeline`, so nothing runs
and behavior is unchanged.

The spec shape (v1)::

    interceptors:
      validators:
        - type: payload_size    # maps to a BUILTIN_VALIDATORS factory
          max_bytes: 1000000    # remaining keys become factory kwargs

Each spec's ``type`` selects a factory from :data:`BUILTIN_VALIDATORS`; the
remaining keys are passed as keyword arguments to that factory. Registration
order follows source order (deterministic).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp_hangar.application.services.validator_pipeline import ValidatorPipeline
from mcp_hangar.application.validators.payload_size import PayloadSizeValidator

if TYPE_CHECKING:
    from mcp_hangar.domain.contracts.validator import IValidator

# Map of config ``type`` name -> factory building a built-in IValidator.
# A factory receives the remaining spec keys as keyword arguments.
BUILTIN_VALIDATORS: dict[str, Callable[..., IValidator]] = {
    "payload_size": lambda **kw: PayloadSizeValidator(**kw),
}


def build_validator_pipeline(specs: list[dict[str, Any]] | None) -> ValidatorPipeline:
    """Build a :class:`ValidatorPipeline` from opt-in validator specs.

    Args:
        specs: A list of spec dicts, each with a ``type`` key selecting a
            built-in validator and remaining keys passed as factory kwargs.
            ``None`` or an empty list yields an empty pipeline (no validators).

    Returns:
        A pipeline with the configured validators registered in source order.

    Raises:
        ValueError: If a spec is missing ``type`` or names an unknown type, or
            if a factory rejects the provided kwargs.
    """
    pipeline = ValidatorPipeline()
    if not specs:
        return pipeline

    for spec in specs:
        params = dict(spec)
        type_name = params.pop("type", None)
        if not type_name:
            raise ValueError(f"interceptor validator spec missing required 'type' key: {spec!r}")
        factory = BUILTIN_VALIDATORS.get(type_name)
        if factory is None:
            known = ", ".join(sorted(BUILTIN_VALIDATORS)) or "(none)"
            raise ValueError(f"unknown interceptor validator type {type_name!r}; known types: {known}")
        pipeline.register(factory(**params))

    return pipeline


__all__ = ["BUILTIN_VALIDATORS", "build_validator_pipeline"]
