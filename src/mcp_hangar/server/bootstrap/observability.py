"""Observability Bootstrap - Initialize tracing and monitoring.

This module handles initialization of:
- OpenTelemetry tracing (distributed tracing)
- Langfuse integration (LLM-specific observability)
- Observability adapters for the application

Configuration via environment variables:
    MCP_TRACING_ENABLED: Enable OpenTelemetry (default: true)
    OTEL_EXPORTER_OTLP_*: standard OTLP exporter settings; their precedence
        over otlp_endpoint is in mcp_hangar.observability.tracing
    OTEL_SERVICE_NAME: Service name (default: mcp-hangar)
    MCP_AUDIT_EXPORT_ENABLED: Export audit records over OTLP to an OTLP endpoint
        set explicitly (default: true). False turns audit export off, not tracing
    MCP_SPAN_ATTRIBUTE_LENGTH_LIMIT: Longest span attribute value, in characters
        (default: 256). OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT, then
        OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT, win when set
    MCP_AUDIT_ATTRIBUTE_LENGTH_LIMIT: Longest OTLP audit attribute value (default:
        256). OTEL_LOGRECORD_ATTRIBUTE_VALUE_LENGTH_LIMIT, then
        OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT, win when set. Both apply only to the
        providers Hangar builds, never to one registered before it
    MCP_LOG_FIELD_LENGTH_LIMIT, MCP_EVENT_TEXT_LENGTH_LIMIT: see logging_config
        and domain.events.base (defaults: 2048 and 4096)
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
      audit:
        enabled: true  # MCP_AUDIT_EXPORT_ENABLED wins over this
      langfuse:
        enabled: true
        public_key: ${LANGFUSE_PUBLIC_KEY}
        secret_key: ${LANGFUSE_SECRET_KEY}
        host: https://cloud.langfuse.com
        sample_rate: 1.0
        scrub_inputs: true
        scrub_outputs: true
"""

from dataclasses import dataclass
import os
import platform
from typing import Any

from ...application.ports.observability import NullObservabilityAdapter, ObservabilityPort
from ...domain.contracts.metrics_publisher import set_default_metrics_publisher
from ...infrastructure.metrics_publisher import PrometheusMetricsPublisher
from ...infrastructure.observability.otlp_audit_exporter import init_audit_log_export, shutdown_audit_log_export
from ...logging_config import get_logger
from .components import create_observability_adapter

logger = get_logger(__name__)


@dataclass
class TracingConfig:
    """Configuration for OpenTelemetry tracing."""

    enabled: bool = True
    # None: nobody chose one, and the OpenTelemetry SDK resolves it -- from the
    # OTEL_EXPORTER_OTLP_* variables, else its own default for the protocol.
    otlp_endpoint: str | None = None
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
    scrub_inputs: bool = True
    scrub_outputs: bool = True


@dataclass
class ObservabilityConfig:
    """Combined observability configuration."""

    tracing: TracingConfig
    langfuse: LangfuseBootstrapConfig
    audit_otlp_endpoint: str | None = None
    """Where OTLP audit records go; None keeps audit export off."""
    audit_export_enabled: bool = True
    """`observability.audit.enabled`: false keeps audit export off whatever the endpoint."""


def _parse_observability_config(config: dict[str, Any]) -> ObservabilityConfig:
    """Parse observability configuration from dict and environment.

    Environment variables take precedence over config file values.
    """
    obs_config = config.get("observability", {})

    # Tracing config
    tracing_dict = obs_config.get("tracing", {})
    tracing = TracingConfig(
        enabled=_get_bool_env("MCP_TRACING_ENABLED", tracing_dict.get("enabled", True)),
        # Still mirrors the env var, which beats the file as before. The trace
        # exporter itself defers to any OTEL_EXPORTER_OTLP_[TRACES_]ENDPOINT:
        # see resolve_otlp_exporter_settings().
        otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", tracing_dict.get("otlp_endpoint")),
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
        scrub_inputs=_get_bool_env("MCP_LANGFUSE_SCRUB_INPUTS", langfuse_dict.get("scrub_inputs", True)),
        scrub_outputs=_get_bool_env("MCP_LANGFUSE_SCRUB_OUTPUTS", langfuse_dict.get("scrub_outputs", True)),
    )

    # Audit records go to the tracing endpoint, but only to one set explicitly,
    # in the env or the file (#1289): `otlp_endpoint` defaults to localhost, and
    # nobody chose that. Not gated on `tracing.enabled`: audit is its own signal,
    # with its own switch (#1327), the env's word beating the file's. A string is
    # read as the env var is: `${VAR:-false}` interpolates to a truthy "false".
    audit_enabled = (obs_config.get("audit") or {}).get("enabled", True)
    if isinstance(audit_enabled, str):
        audit_enabled = audit_enabled.lower() in ("true", "1", "yes")
    audit_enabled = _get_bool_env("MCP_AUDIT_EXPORT_ENABLED", bool(audit_enabled))
    explicit = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") is not None or "otlp_endpoint" in tracing_dict
    audit_otlp_endpoint = tracing.otlp_endpoint if audit_enabled and explicit and tracing.otlp_endpoint else None

    return ObservabilityConfig(
        tracing=tracing,
        langfuse=langfuse,
        audit_otlp_endpoint=audit_otlp_endpoint,
        audit_export_enabled=audit_enabled,
    )


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
        True if Hangar registered its own tracer provider. False when another
        provider was registered first: Hangar's spans then go through that one.
    """
    if not config.enabled:
        logger.info("tracing_disabled_by_config")
        from ...observability.tracing import disable_tracing

        disable_tracing()
        return False

    try:
        from ...domain.events import current_instance_id
        from ...observability.tracing import init_tracing as otel_init_tracing

        # No init line here: `tracing_initialized` is logged once, by
        # otel_init_tracing, with the exporters it actually attached.
        return otel_init_tracing(
            service_name=config.service_name,
            otlp_endpoint=config.otlp_endpoint,
            jaeger_host=config.jaeger_host,
            jaeger_port=config.jaeger_port,
            console_export=config.console_export,
            service_instance_id=current_instance_id(),
        )

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
        adapter = create_observability_adapter(config)
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


def init_metrics_publisher() -> None:
    """Connect the domain's metrics port to its Prometheus adapter.

    A named function rather than two lines inline in `bootstrap`, because this
    wiring is the whole defect it fixes and a test needs to be able to assert it
    without standing up the application. `PrometheusMetricsPublisher` appeared
    exactly once in the codebase -- at its own class statement -- so every
    McpServer used the Null object and the cold-start histogram
    (`mcp_hangar_mcp_server_cold_start_seconds`, which metrics.py calls the
    critical UX metric) was never observed.

    Must run before McpServer instances are constructed: they read the default
    at construction time.
    """
    set_default_metrics_publisher(PrometheusMetricsPublisher())
    logger.debug("metrics_publisher_wired", adapter="PrometheusMetricsPublisher")

    # Two metrics that were registered and never given a value, so `/metrics`
    # carried their TYPE header and no sample, forever (#1163). Both are the
    # standard shape every Prometheus deployment expects: `*_build` to join a
    # version onto a series, and a process start time to compute uptime.
    import time

    from mcp_hangar import __version__
    from ...metrics import BUILD_INFO, PROCESS_START_TIME

    BUILD_INFO.info(version=__version__, python=platform.python_version())
    PROCESS_START_TIME.set(time.time())


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

    # The audit log pipeline: its own provider, beside tracing's, not inside it.
    # Before `init_event_handlers`, which selects the audit exporter from it.
    # Switched off, no endpoint reaches it, so it builds and turns on nothing.
    if not obs_config.audit_export_enabled:
        logger.info("audit_log_export_disabled_by_config")
    init_audit_log_export(obs_config.audit_otlp_endpoint, obs_config.tracing.service_name)

    # Initialize Langfuse
    observability_adapter = init_langfuse(obs_config.langfuse)

    logger.info(
        "observability_initialized",
        tracing_enabled=tracing_enabled,
        audit_log_export=obs_config.audit_otlp_endpoint is not None,
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

    # Shutdown OpenTelemetry tracing. shutdown_tracing() logs its own outcome:
    # it shuts down only a provider Hangar registered, within a bound.
    try:
        from ...observability.tracing import shutdown_tracing

        shutdown_tracing()
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing shutdown must not crash application
        logger.warning("tracing_shutdown_error", error=str(e))

    # Audit export, separately bounded. It too shuts down only its own provider.
    shutdown_audit_log_export()
