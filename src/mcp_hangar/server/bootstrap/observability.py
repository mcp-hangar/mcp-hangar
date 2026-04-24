"""Observability Bootstrap - Initialize tracing and monitoring.

This module handles initialization of:
- OpenTelemetry tracing (distributed tracing)
- Langfuse integration (LLM-specific observability)
- Observability adapters for the application

Configuration via environment variables:
    MCP_TRACING_ENABLED: Enable OpenTelemetry (default: true)
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (default: http://localhost:4317)
    OTEL_SERVICE_NAME: Service name (default: mcp-hangar)
    MCP_LANGFUSE_ENABLED: Enable Langfuse (default: false)
    LANGFUSE_PUBLIC_KEY: Langfuse public key
    LANGFUSE_SECRET_KEY: Langfuse secret key
    LANGFUSE_HOST: Langfuse host (default: https://cloud.langfuse.com)
    MCP_LANGFUSE_SAMPLE_RATE: Sample rate 0.0-1.0 (default: 1.0)

Or via config.yaml:
    observability:
      tracing:
        enabled: true
        otlp_endpoint: http://localhost:4317
        service_name: mcp-hangar
      langfuse:
        enabled: true
        public_key: ${LANGFUSE_PUBLIC_KEY}
        secret_key: ${LANGFUSE_SECRET_KEY}
        host: https://cloud.langfuse.com
        sample_rate: 1.0
        scrub_inputs: false
        scrub_outputs: false
"""

from dataclasses import dataclass
import os
from typing import Any

from ...application.ports.observability import NullObservabilityAdapter, ObservabilityPort
from ...logging_config import get_logger
from .enterprise import create_enterprise_observability_adapter

logger = get_logger(__name__)


@dataclass
class TracingConfig:
    """Configuration for OpenTelemetry tracing."""

    enabled: bool = True
    otlp_endpoint: str = "http://localhost:4317"
    service_name: str = "mcp-hangar"
    jaeger_host: str | None = None
    jaeger_port: int = 6831
    console_export: bool = False


@dataclass
class LangfuseBootstrapConfig:
    """Configuration for Langfuse integration."""

    enabled: bool = False
    public_key: str = ""
    secret_key: str = ""
    host: str = "https://cloud.langfuse.com"
    sample_rate: float = 1.0
    scrub_inputs: bool = False
    scrub_outputs: bool = False


@dataclass
class ObservabilityConfig:
    """Combined observability configuration."""

    tracing: TracingConfig
    langfuse: LangfuseBootstrapConfig


def _parse_observability_config(config: dict[str, Any]) -> ObservabilityConfig:
    """Parse observability configuration from dict and environment.

    Environment variables take precedence over config file values.
    """
    obs_config = config.get("observability", {})

    # Tracing config
    tracing_dict = obs_config.get("tracing", {})
    tracing = TracingConfig(
        enabled=_get_bool_env("MCP_TRACING_ENABLED", tracing_dict.get("enabled", True)),
        otlp_endpoint=os.getenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            tracing_dict.get("otlp_endpoint", "http://localhost:4317"),
        ),
        service_name=os.getenv(
            "OTEL_SERVICE_NAME",
            tracing_dict.get("service_name", "mcp-hangar"),
        ),
        jaeger_host=os.getenv("JAEGER_HOST", tracing_dict.get("jaeger_host")),
        jaeger_port=int(os.getenv("JAEGER_PORT", str(tracing_dict.get("jaeger_port", 6831)))),
        console_export=_get_bool_env("MCP_TRACING_CONSOLE", tracing_dict.get("console_export", False)),
    )

    # Langfuse config
    langfuse_dict = obs_config.get("langfuse", {})
    langfuse = LangfuseBootstrapConfig(
        enabled=_get_bool_env("MCP_LANGFUSE_ENABLED", langfuse_dict.get("enabled", False)),
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY", _expand_env(langfuse_dict.get("public_key", ""))),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY", _expand_env(langfuse_dict.get("secret_key", ""))),
        host=os.getenv("LANGFUSE_HOST", langfuse_dict.get("host", "https://cloud.langfuse.com")),
        sample_rate=float(os.getenv("MCP_LANGFUSE_SAMPLE_RATE", str(langfuse_dict.get("sample_rate", 1.0)))),
        scrub_inputs=_get_bool_env("MCP_LANGFUSE_SCRUB_INPUTS", langfuse_dict.get("scrub_inputs", False)),
        scrub_outputs=_get_bool_env("MCP_LANGFUSE_SCRUB_OUTPUTS", langfuse_dict.get("scrub_outputs", False)),
    )

    return ObservabilityConfig(tracing=tracing, langfuse=langfuse)


def _get_bool_env(key: str, default: bool) -> bool:
    """Get boolean from environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


def _expand_env(value: str) -> str:
    """Expand ${VAR} patterns in string."""
    if not value:
        return value
    if value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.getenv(env_var, "")
    return value


def init_tracing(config: TracingConfig) -> bool:
    """Initialize OpenTelemetry tracing.

    Args:
        config: Tracing configuration.

    Returns:
        True if tracing was initialized successfully.
    """
    if not config.enabled:
        logger.info("tracing_disabled_by_config")
        return False

    try:
        from ...observability.tracing import init_tracing as otel_init_tracing

        result = otel_init_tracing(
            service_name=config.service_name,
            otlp_endpoint=config.otlp_endpoint,
            jaeger_host=config.jaeger_host,
            jaeger_port=config.jaeger_port,
            console_export=config.console_export,
        )

        if result:
            logger.info(
                "tracing_initialized",
                service_name=config.service_name,
                otlp_endpoint=config.otlp_endpoint,
            )
        return result

    except ImportError:
        logger.info(
            "tracing_disabled_otel_not_installed",
            hint="Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp",
        )
        return False
    except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing init failure must not crash application
        logger.warning("tracing_initialization_failed", error=str(e))
        return False


def init_langfuse(config: LangfuseBootstrapConfig) -> ObservabilityPort:
    """Initialize Langfuse observability adapter.

    Args:
        config: Langfuse configuration.

    Returns:
        ObservabilityPort implementation (LangfuseObservabilityAdapter or NullObservabilityAdapter).
    """
    if not config.enabled:
        logger.info("langfuse_disabled_by_config")
        return NullObservabilityAdapter()

    if not config.public_key or not config.secret_key:
        logger.warning(
            "langfuse_disabled_missing_credentials",
            has_public_key=bool(config.public_key),
            has_secret_key=bool(config.secret_key),
        )
        return NullObservabilityAdapter()

    try:
        adapter = create_enterprise_observability_adapter(config)
        if adapter is None:
            raise ImportError
        logger.info(
            "langfuse_initialized",
            host=config.host,
            sample_rate=config.sample_rate,
        )
        return adapter

    except ImportError:
        logger.info(
            "langfuse_disabled_not_installed",
            hint="Install with: pip install mcp-hangar[observability]",
        )
        return NullObservabilityAdapter()
    except ValueError as e:
        logger.warning("langfuse_config_invalid", error=str(e))
        return NullObservabilityAdapter()
    except Exception as e:  # noqa: BLE001 -- fault-barrier: langfuse init failure must not crash application
        logger.warning("langfuse_initialization_failed", error=str(e))
        return NullObservabilityAdapter()


def init_observability(config: dict[str, Any]) -> tuple[ObservabilityConfig, ObservabilityPort]:
    """Initialize all observability components.

    Args:
        config: Full application configuration dict.

    Returns:
        Tuple of (ObservabilityConfig, ObservabilityPort adapter).
    """
    obs_config = _parse_observability_config(config)

    # Initialize OpenTelemetry tracing
    tracing_enabled = init_tracing(obs_config.tracing)

    # Initialize Langfuse
    observability_adapter = init_langfuse(obs_config.langfuse)

    logger.info(
        "observability_initialized",
        tracing_enabled=tracing_enabled,
        langfuse_enabled=obs_config.langfuse.enabled
        and not isinstance(observability_adapter, NullObservabilityAdapter),
    )

    return obs_config, observability_adapter


def shutdown_observability(adapter: ObservabilityPort | None) -> None:
    """Shutdown observability components gracefully.

    Args:
        adapter: ObservabilityPort adapter to shutdown.
    """
    # Shutdown Langfuse adapter
    if adapter is not None:
        try:
            adapter.shutdown()
            logger.debug("langfuse_shutdown_complete")
        except Exception as e:  # noqa: BLE001 -- fault-barrier: langfuse shutdown must not crash application
            logger.warning("langfuse_shutdown_error", error=str(e))

    # Shutdown OpenTelemetry tracing
    try:
        from ...observability.tracing import shutdown_tracing

        shutdown_tracing()
        logger.debug("tracing_shutdown_complete")
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing shutdown must not crash application
        logger.warning("tracing_shutdown_error", error=str(e))
