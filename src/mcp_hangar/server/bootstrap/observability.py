"""Observability Bootstrap - Initialize tracing and monitoring.

This module handles initialization of:
- OpenTelemetry tracing (distributed tracing)
- OTLP audit log export

Configuration via environment variables:
    MCP_TRACING_ENABLED: Enable OpenTelemetry (default: true)
    MCP_TRACING_CALLER_IDS: Put the caller's user, agent and session ids on
        spans (default: false). Tenant, principal type and correlation id are
        on spans either way; audit records carry identity either way
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
    Langfuse takes these spans over OTLP: point OTEL_EXPORTER_OTLP_TRACES_*
        at its OTLP endpoint. The Langfuse adapter and its MCP_LANGFUSE_* /
        HANGAR_LANGFUSE_* settings were removed (#1683); see
        _refuse_or_warn_on_removed_langfuse_settings

Or via config.yaml:
    observability:
      tracing:
        enabled: true
        otlp_endpoint: http://localhost:4317
        service_name: mcp-hangar
        caller_ids: false  # MCP_TRACING_CALLER_IDS wins over this
      audit:
        enabled: true  # MCP_AUDIT_EXPORT_ENABLED wins over this
"""

import os
import platform
from dataclasses import dataclass
from typing import Any

from ...domain.contracts.l7_verdict_observer import set_default_l7_verdict_observer
from ...domain.contracts.metrics_publisher import set_default_metrics_publisher
from ...domain.exceptions import ConfigurationError
from ...infrastructure.metrics_publisher import PrometheusMetricsPublisher
from ...infrastructure.observability.l7_verdicts import ContextL7VerdictObserver
from ...infrastructure.observability.otlp_audit_exporter import init_audit_log_export, shutdown_audit_log_export
from ...logging_config import get_logger

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
    caller_ids: bool = False
    """`observability.tracing.caller_ids`: user, agent and session ids on spans (#1580)."""


@dataclass
class ObservabilityConfig:
    """Combined observability configuration."""

    tracing: TracingConfig
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
        caller_ids=_get_bool_env("MCP_TRACING_CALLER_IDS", _file_bool(tracing_dict.get("caller_ids", False))),
    )

    # Audit records go to the tracing endpoint, but only to one set explicitly,
    # in the env or the file (#1289): `otlp_endpoint` defaults to localhost, and
    # nobody chose that. Not gated on `tracing.enabled`: audit is its own signal,
    # with its own switch (#1327), the env's word beating the file's. A string is
    # read as the env var is: `${VAR:-false}` interpolates to a truthy "false".
    audit_enabled = _get_bool_env(
        "MCP_AUDIT_EXPORT_ENABLED", _file_bool((obs_config.get("audit") or {}).get("enabled", True))
    )
    explicit = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") is not None or "otlp_endpoint" in tracing_dict
    audit_otlp_endpoint = tracing.otlp_endpoint if audit_enabled and explicit and tracing.otlp_endpoint else None

    return ObservabilityConfig(
        tracing=tracing,
        audit_otlp_endpoint=audit_otlp_endpoint,
        audit_export_enabled=audit_enabled,
    )


def _file_bool(value: Any) -> bool:
    """A config-file switch, a string read as the env var is: `${VAR:-false}` is not truthy."""
    if isinstance(value, str):
        return value.lower() in ("true", "1", "yes")
    return bool(value)


def _get_bool_env(key: str, default: bool) -> bool:
    """Get boolean from environment variable."""
    value = os.getenv(key)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes")


# The Langfuse adapter's settings, removed with it (#1683). It was built and never
# called after 2.22.0, so none of them did anything; Langfuse takes Hangar's spans
# over OTLP instead. LANGFUSE_PUBLIC_KEY / _SECRET_KEY / _HOST are not here: they
# are the Langfuse SDK's own variables, and a co-located application may set them.
_REMOVED_LANGFUSE_SCRUB_ENV = (
    "MCP_LANGFUSE_SCRUB_INPUTS",
    "MCP_LANGFUSE_SCRUB_OUTPUTS",
    "HANGAR_LANGFUSE_SCRUB_INPUTS",
    "HANGAR_LANGFUSE_SCRUB_OUTPUTS",
)
_REMOVED_LANGFUSE_ENV = (
    "MCP_LANGFUSE_ENABLED",
    "MCP_LANGFUSE_SAMPLE_RATE",
    "HANGAR_LANGFUSE_ENABLED",
    "HANGAR_LANGFUSE_SAMPLE_RATE",
)
LANGFUSE_OVER_OTLP = (
    "Langfuse takes Hangar's spans over OTLP: set OTEL_EXPORTER_OTLP_TRACES_ENDPOINT to its OTLP "
    "traces endpoint, OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/protobuf and the Basic auth header in "
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS (examples/langfuse/README.md)"
)


def _refuse_or_warn_on_removed_langfuse_settings(config: dict[str, Any]) -> None:
    """Refuse a removed Langfuse scrub setting; warn about the other removed ones.

    A scrub setting is refused whatever its value: it asked Hangar to keep
    payloads away from a third party, and a setting that silently stops
    applying is the failure #1655 was about. Hangar's spans carry no tool
    arguments or results (#1276), so there is nothing left for it to scrub;
    redacting what spans do carry belongs in the OpenTelemetry pipeline. The
    rest of an `observability.langfuse` block is named by the config schema.
    """
    langfuse = (config.get("observability") or {}).get("langfuse")
    scrub = [key for key in _REMOVED_LANGFUSE_SCRUB_ENV if key in os.environ]
    if isinstance(langfuse, dict):
        scrub += [f"observability.langfuse.{key}" for key in ("scrub_inputs", "scrub_outputs") if key in langfuse]
    if scrub:
        raise ConfigurationError(
            f"{', '.join(scrub)}: the Langfuse adapter and its scrub settings were removed (#1683), so this "
            "no longer applies. Hangar's spans carry no tool arguments or results; to redact what they do "
            "carry, use an OpenTelemetry Collector processor (attributes or redaction) in front of Langfuse. "
            f"{LANGFUSE_OVER_OTLP}. Delete the setting to start.",
            details={"settings": scrub},
        )

    stale = [key for key in _REMOVED_LANGFUSE_ENV if key in os.environ]
    if stale:
        logger.warning("langfuse_settings_removed", settings=stale, replacement=LANGFUSE_OVER_OTLP)


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
        started = otel_init_tracing(
            service_name=config.service_name,
            otlp_endpoint=config.otlp_endpoint,
            jaeger_host=config.jaeger_host,
            jaeger_port=config.jaeger_port,
            console_export=config.console_export,
            service_instance_id=current_instance_id(),
        )
        if started:
            _install_startup_observer()
        return started

    except ImportError:
        logger.info(
            "tracing_disabled_otel_not_installed",
            hint="Install with: pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp",
        )
        return False
    except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing init failure must not crash application
        logger.warning("tracing_initialization_failed", error=str(e))
        return False


def _install_startup_observer() -> None:
    """Let the aggregate report who starts a server and who waits for it (#1279).

    Installed here, beside the tracer it writes to, rather than left for a
    caller to remember: an adapter nothing installs is what
    `TracedMcpServerService` was, and the point of #1278 was to stop shipping
    those. Fault-barriered, because a gateway that will not start because its
    telemetry could not be wired has its priorities backwards.
    """
    try:
        from ...domain.contracts.startup_observer import set_startup_observer
        from ...infrastructure.observability.startup_spans import StartupSpanAdapter, aggregate_observer

        set_startup_observer(aggregate_observer(StartupSpanAdapter()))
        logger.debug("startup_observer_installed")
    except Exception as e:  # noqa: BLE001 -- fault-barrier: telemetry wiring must not crash boot
        logger.warning("startup_observer_install_failed", error=str(e))


def init_l7_verdict_observer() -> None:
    """Connect the aggregate's L7 verdict port to the adapter the batch executor reads (#1295).

    Without it every verdict goes to the Null object and `batch.call.<tool>`
    carries no `hangar.l7.*`, as the cold-start metric carried nothing while
    its publisher was never constructed (#1567).
    """
    set_default_l7_verdict_observer(ContextL7VerdictObserver())


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


def init_observability(config: dict[str, Any]) -> ObservabilityConfig:
    """Initialize all observability components.

    Args:
        config: Full application configuration dict.

    Returns:
        The parsed ObservabilityConfig.

    Raises:
        ConfigurationError: If a removed Langfuse scrub setting is present.
    """
    # First, so a refused config has exported nothing.
    _refuse_or_warn_on_removed_langfuse_settings(config)

    obs_config = _parse_observability_config(config)

    # Read here once, not per call: the executor asks the tracing module (#1580).
    from ...observability.tracing import set_caller_ids_on_spans

    set_caller_ids_on_spans(obs_config.tracing.caller_ids)
    if obs_config.tracing.caller_ids:
        logger.info("tracing_caller_ids_enabled_by_config")

    # Initialize OpenTelemetry tracing
    tracing_enabled = init_tracing(obs_config.tracing)

    # The audit log pipeline: its own provider, beside tracing's, not inside it.
    # Before `init_event_handlers`, which selects the audit exporter from it.
    # Switched off, no endpoint reaches it, so it builds and turns on nothing.
    if not obs_config.audit_export_enabled:
        logger.info("audit_log_export_disabled_by_config")
    init_audit_log_export(obs_config.audit_otlp_endpoint, obs_config.tracing.service_name)

    logger.info(
        "observability_initialized",
        tracing_enabled=tracing_enabled,
        audit_log_export=obs_config.audit_otlp_endpoint is not None,
    )

    return obs_config


def shutdown_observability() -> None:
    """Shutdown observability components gracefully."""
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
