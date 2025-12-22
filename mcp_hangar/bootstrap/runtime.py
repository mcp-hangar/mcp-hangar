"""Bootstrap helpers for wiring runtime dependencies.

This module centralizes object graph creation (composition root helpers) so that
the rest of the codebase can avoid module-level singletons and implicit globals.

It intentionally returns plain objects (repository, buses, security plumbing)
without starting any background threads.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional

from ..application.event_handlers import get_security_handler
from ..domain.repository import InMemoryProviderRepository, IProviderRepository
from ..domain.security.input_validator import InputValidator
from ..domain.security.rate_limiter import get_rate_limiter, RateLimitConfig
from ..infrastructure.command_bus import CommandBus, get_command_bus
from ..infrastructure.event_bus import EventBus, get_event_bus
from ..infrastructure.query_bus import get_query_bus, QueryBus


@dataclass(frozen=True)
class Runtime:
    """Container for runtime dependencies."""

    repository: IProviderRepository
    event_bus: EventBus
    command_bus: CommandBus
    query_bus: QueryBus

    rate_limit_config: RateLimitConfig
    rate_limiter: object  # keeping loose to avoid importing rate limiter protocol/type

    input_validator: InputValidator
    security_handler: object  # keeping loose to avoid importing handler type


def create_runtime(
    *,
    repository: Optional[IProviderRepository] = None,
    event_bus: Optional[EventBus] = None,
    command_bus: Optional[CommandBus] = None,
    query_bus: Optional[QueryBus] = None,
    env: Optional[dict[str, str]] = None,
) -> Runtime:
    """Create runtime dependencies explicitly.

    Args:
        repository: Optional repository override (useful for tests).
        event_bus: Optional event bus override.
        command_bus: Optional command bus override.
        query_bus: Optional query bus override.
        env: Optional environment mapping (defaults to os.environ).

    Returns:
        Runtime container.
    """
    env = env or os.environ

    repo = repository or InMemoryProviderRepository()
    eb = event_bus or get_event_bus()
    cb = command_bus or get_command_bus()
    qb = query_bus or get_query_bus()

    rate_limit_config = RateLimitConfig(
        requests_per_second=float(env.get("MCP_RATE_LIMIT_RPS", "10")),
        burst_size=int(env.get("MCP_RATE_LIMIT_BURST", "20")),
    )
    rate_limiter = get_rate_limiter(rate_limit_config)

    input_validator = InputValidator(
        allow_absolute_paths=env.get("MCP_ALLOW_ABSOLUTE_PATHS", "false").lower() == "true",
    )

    security_handler = get_security_handler()

    return Runtime(
        repository=repo,
        event_bus=eb,
        command_bus=cb,
        query_bus=qb,
        rate_limit_config=rate_limit_config,
        rate_limiter=rate_limiter,
        input_validator=input_validator,
        security_handler=security_handler,
    )
