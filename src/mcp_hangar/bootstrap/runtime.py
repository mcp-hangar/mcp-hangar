"""Bootstrap helpers for wiring runtime dependencies.

This module centralizes object graph creation (composition root helpers) so that
the rest of the codebase can avoid module-level singletons and implicit globals.

It intentionally returns plain objects (repository, buses, security plumbing)
without starting any background threads.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol, runtime_checkable

from ..application.event_handlers import get_security_handler
from ..application.ports.observability import NullObservabilityAdapter, ObservabilityPort
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

# =============================================================================
# Protocol Interfaces for Runtime Dependencies
# =============================================================================


@runtime_checkable
class IRateLimiter(Protocol):
    """Interface for rate limiter."""

    def consume(self, key: str) -> Any:
        """Check rate limit for a key."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics."""
        ...


@runtime_checkable
class ISecurityHandler(Protocol):
    """Interface for security event handler."""

    def handle(self, event: Any) -> None:
        """Handle a security event."""
        ...

    def log_rate_limit_exceeded(self, limit: int, window_seconds: int) -> None:
        """Log rate limit exceeded."""
        ...

    def log_validation_failed(
        self,
        field: str,
        message: str,
        provider_id: str | None = None,
        value: str | None = None,
    ) -> None:
        """Log validation failure."""
        ...


@runtime_checkable
class IConfigRepository(Protocol):
    """Interface for provider config repository."""

    async def save(self, config: Any) -> None:
        """Save a configuration."""
        ...

    async def get(self, provider_id: str) -> Any | None:
        """Get configuration by provider ID."""
        ...

    async def get_all(self) -> list[Any]:
        """Get all configurations."""
        ...


@runtime_checkable
class IAuditRepository(Protocol):
    """Interface for audit repository."""

    async def append(self, entry: Any) -> None:
        """Append an audit entry."""
        ...


@dataclass(frozen=True)
class PersistenceConfig:
    """Configuration for persistence layer."""

    enabled: bool = False
    database_path: str = "data/mcp_hangar.db"
    enable_wal: bool = True
    auto_recover: bool = True


@dataclass(frozen=True)
class ObservabilityConfig:
    """Configuration for observability integrations.

    Supports Langfuse for LLM observability and tracing.

    Attributes:
        langfuse_enabled: Whether Langfuse integration is active.
        langfuse_public_key: Langfuse public API key.
        langfuse_secret_key: Langfuse secret API key.
        langfuse_host: Langfuse host URL.
        langfuse_sample_rate: Fraction of traces to sample (0.0 to 1.0).
        langfuse_scrub_inputs: Whether to redact sensitive inputs.
        langfuse_scrub_outputs: Whether to redact sensitive outputs.
    """

    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"
    langfuse_sample_rate: float = 1.0
    langfuse_scrub_inputs: bool = False
    langfuse_scrub_outputs: bool = False


@dataclass(frozen=True)
class Runtime:
    """Container for runtime dependencies.

    Uses Protocol interfaces for type safety while maintaining flexibility.
    """

    repository: IProviderRepository
    event_bus: EventBus
    command_bus: CommandBus
    query_bus: QueryBus

    rate_limit_config: RateLimitConfig
    rate_limiter: IRateLimiter

    input_validator: InputValidator
    security_handler: ISecurityHandler

    # Persistence components (optional)
    persistence_config: PersistenceConfig | None = None
    database: Database | None = None
    config_repository: IConfigRepository | None = None
    audit_repository: IAuditRepository | None = None
    recovery_service: RecoveryService | None = None

    # Observability components (optional)
    observability_config: ObservabilityConfig | None = None
    observability: ObservabilityPort | None = None


def create_runtime(
    *,
    repository: IProviderRepository | None = None,
    event_bus: EventBus | None = None,
    command_bus: CommandBus | None = None,
    query_bus: QueryBus | None = None,
    persistence_config: PersistenceConfig | None = None,
    observability_config: ObservabilityConfig | None = None,
    env: dict[str, str] | None = None,
) -> Runtime:
    """Create runtime dependencies explicitly.

    Args:
        repository: Optional repository override (useful for tests).
        event_bus: Optional event bus override.
        command_bus: Optional command bus override.
        query_bus: Optional query bus override.
        persistence_config: Optional persistence configuration.
        observability_config: Optional observability configuration.
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

    # Configure observability if enabled
    langfuse_enabled = env.get("HANGAR_LANGFUSE_ENABLED", "false").lower() == "true"

    if observability_config is None and langfuse_enabled:
        observability_config = ObservabilityConfig(
            langfuse_enabled=True,
            langfuse_public_key=env.get("LANGFUSE_PUBLIC_KEY", ""),
            langfuse_secret_key=env.get("LANGFUSE_SECRET_KEY", ""),
            langfuse_host=env.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            langfuse_sample_rate=float(env.get("HANGAR_LANGFUSE_SAMPLE_RATE", "1.0")),
            langfuse_scrub_inputs=env.get("HANGAR_LANGFUSE_SCRUB_INPUTS", "false").lower() == "true",
            langfuse_scrub_outputs=env.get("HANGAR_LANGFUSE_SCRUB_OUTPUTS", "false").lower() == "true",
        )

    observability: ObservabilityPort = NullObservabilityAdapter()

    if observability_config and observability_config.langfuse_enabled:
        try:
            from ..infrastructure.observability import LangfuseConfig, LangfuseObservabilityAdapter

            langfuse_config = LangfuseConfig(
                enabled=True,
                public_key=observability_config.langfuse_public_key,
                secret_key=observability_config.langfuse_secret_key,
                host=observability_config.langfuse_host,
                sample_rate=observability_config.langfuse_sample_rate,
                scrub_inputs=observability_config.langfuse_scrub_inputs,
                scrub_outputs=observability_config.langfuse_scrub_outputs,
            )
            observability = LangfuseObservabilityAdapter(langfuse_config)
        except ImportError:
            import logging

            logging.getLogger(__name__).warning(
                "Langfuse enabled but package not installed. Install with: pip install mcp-hangar[observability]"
            )

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
        observability_config=observability_config,
        observability=observability,
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
    if runtime.observability:
        runtime.observability.shutdown()

    if runtime.database:
        await runtime.database.close()
