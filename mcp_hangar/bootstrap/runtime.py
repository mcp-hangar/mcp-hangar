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
from ..infrastructure.persistence import (
    Database,
    DatabaseConfig,
    InMemoryAuditRepository,
    InMemoryProviderConfigRepository,
    RecoveryService,
    SQLiteAuditRepository,
    SQLiteProviderConfigRepository,
)
from ..infrastructure.query_bus import get_query_bus, QueryBus


@dataclass(frozen=True)
class PersistenceConfig:
    """Configuration for persistence layer."""

    enabled: bool = False
    database_path: str = "data/mcp_hangar.db"
    enable_wal: bool = True
    auto_recover: bool = True


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

    # Persistence components (optional)
    persistence_config: Optional[PersistenceConfig] = None
    database: Optional[Database] = None
    config_repository: Optional[object] = None  # IProviderConfigRepository
    audit_repository: Optional[object] = None  # IAuditRepository
    recovery_service: Optional[RecoveryService] = None


def create_runtime(
    *,
    repository: Optional[IProviderRepository] = None,
    event_bus: Optional[EventBus] = None,
    command_bus: Optional[CommandBus] = None,
    query_bus: Optional[QueryBus] = None,
    persistence_config: Optional[PersistenceConfig] = None,
    env: Optional[dict[str, str]] = None,
) -> Runtime:
    """Create runtime dependencies explicitly.

    Args:
        repository: Optional repository override (useful for tests).
        event_bus: Optional event bus override.
        command_bus: Optional command bus override.
        query_bus: Optional query bus override.
        persistence_config: Optional persistence configuration.
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

    # Configure persistence if enabled
    persistence_enabled = env.get("MCP_PERSISTENCE_ENABLED", "false").lower() == "true"

    if persistence_config is None and persistence_enabled:
        persistence_config = PersistenceConfig(
            enabled=True,
            database_path=env.get("MCP_DATABASE_PATH", "data/mcp_hangar.db"),
            enable_wal=env.get("MCP_DATABASE_WAL", "true").lower() == "true",
            auto_recover=env.get("MCP_AUTO_RECOVER", "true").lower() == "true",
        )

    database = None
    config_repository = None
    audit_repository = None
    recovery_service = None

    if persistence_config and persistence_config.enabled:
        db_config = DatabaseConfig(
            path=persistence_config.database_path,
            enable_wal=persistence_config.enable_wal,
        )
        database = Database(db_config)
        config_repository = SQLiteProviderConfigRepository(database)
        audit_repository = SQLiteAuditRepository(database)
        recovery_service = RecoveryService(
            database=database,
            provider_repository=repo,
            config_repository=config_repository,
            audit_repository=audit_repository,
        )
    else:
        # Use in-memory repositories for non-persistent mode
        config_repository = InMemoryProviderConfigRepository()
        audit_repository = InMemoryAuditRepository()

    return Runtime(
        repository=repo,
        event_bus=eb,
        command_bus=cb,
        query_bus=qb,
        rate_limit_config=rate_limit_config,
        rate_limiter=rate_limiter,
        input_validator=input_validator,
        security_handler=security_handler,
        persistence_config=persistence_config,
        database=database,
        config_repository=config_repository,
        audit_repository=audit_repository,
        recovery_service=recovery_service,
    )


async def initialize_runtime(runtime: Runtime) -> None:
    """Initialize runtime async components.

    Should be called during application startup.

    Args:
        runtime: Runtime container to initialize
    """
    if runtime.database:
        await runtime.database.initialize()

    if runtime.recovery_service and runtime.persistence_config:
        if runtime.persistence_config.auto_recover:
            await runtime.recovery_service.recover_providers()


async def shutdown_runtime(runtime: Runtime) -> None:
    """Shutdown runtime async components.

    Should be called during application shutdown.

    Args:
        runtime: Runtime container to shutdown
    """
    if runtime.database:
        await runtime.database.close()

