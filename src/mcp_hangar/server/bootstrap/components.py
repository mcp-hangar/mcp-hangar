"""Optional auth / approval component loader.

Bootstraps the in-core auth and approval modules when they are configured and
available. Historically this was a plugin boundary that discovered a *separate*
optional package via ``mcp_hangar.extensions`` entry points; that package
was retired, so the indirection (provider registry + entry-point discovery) is
gone and the built-in modules are loaded directly. The public functions and the
``ServerComponents`` container are unchanged so callers stay stable.
"""

# pyright: reportExplicitAny=false, reportAny=false, reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable

from ...application.ports.observability import ObservabilityPort
from ...infrastructure.event_store import get_event_store
from ...logging_config import get_logger

logger = get_logger(__name__)


class _FallbackAuthComponents:
    """Stub AuthComponents used when the auth module is unavailable."""

    enabled: bool = False
    api_key_store: Any = None
    role_store: Any = None
    tap_store: Any = None
    authn_middleware: Any = None
    authz_middleware: Any = None


class _FallbackNullAuthComponents(_FallbackAuthComponents):
    """Null/noop auth implementation used when the auth module is unavailable."""


def _fallback_bootstrap_auth(_config: Any = None, **_kwargs: Any) -> _FallbackNullAuthComponents:
    """Return noop auth components when the auth module is not installed."""
    return _FallbackNullAuthComponents()


def _fallback_parse_auth_config(_raw: dict[str, Any] | None = None) -> None:
    """Return empty config when the auth module is not installed."""
    return None


@dataclass(frozen=True)
class AuthCompatibilityExports:
    """Legacy auth exports re-exposed from ``mcp_hangar.server.bootstrap``."""

    AuthComponents: type[Any]
    NullAuthComponents: type[Any]
    bootstrap_auth: Callable[..., Any]
    parse_auth_config: Callable[[dict[str, Any] | None], Any]
    auth_available: bool


@dataclass
class ServerComponents:
    """Container for the optional auth / approval component instances."""

    auth_components: Any = None
    approval_service: Any = None


def _import_attribute(module_name: str, attribute_name: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def get_auth_compat_exports() -> AuthCompatibilityExports:
    """Resolve legacy auth compatibility exports from the auth module or fallback."""
    try:
        auth_components = _import_attribute("mcp_hangar.auth.bootstrap", "AuthComponents")
        null_auth_components = _import_attribute("mcp_hangar.auth.bootstrap", "NullAuthComponents")
        bootstrap_auth = _import_attribute("mcp_hangar.auth.bootstrap", "bootstrap_auth")
        parse_auth_config = _import_attribute("mcp_hangar.auth.config", "parse_auth_config")
    except ImportError:
        return AuthCompatibilityExports(
            AuthComponents=_FallbackAuthComponents,
            NullAuthComponents=_FallbackNullAuthComponents,
            bootstrap_auth=_fallback_bootstrap_auth,
            parse_auth_config=_fallback_parse_auth_config,
            auth_available=False,
        )

    return AuthCompatibilityExports(
        AuthComponents=cast(type[Any], auth_components),
        NullAuthComponents=cast(type[Any], null_auth_components),
        bootstrap_auth=cast(Callable[..., Any], bootstrap_auth),
        parse_auth_config=cast(Callable[[dict[str, Any] | None], Any], parse_auth_config),
        auth_available=True,
    )


def load_components(
    config: dict[str, Any],
    event_bus: Any = None,
    event_publisher: Any = None,
) -> ServerComponents:
    """Load the optional auth components when auth is configured and available.

    Args:
        config: Full application configuration dictionary.
        event_bus: Optional event bus for auth module wiring.
        event_publisher: Optional callable for publishing domain events.

    Returns:
        ServerComponents populated when auth is enabled, otherwise empty.
    """
    exports = get_auth_compat_exports()
    if not exports.auth_available:
        logger.info("optional_components_unavailable", reason="auth_module_not_installed")
        return ServerComponents()

    auth_config = exports.parse_auth_config(config.get("auth"))
    if auth_config is None or not getattr(auth_config, "enabled", False):
        return ServerComponents()

    auth_components = exports.bootstrap_auth(
        auth_config,
        event_publisher=event_publisher,
        event_store=get_event_store(),
        event_bus=event_bus,
    )
    components = ServerComponents(auth_components=auth_components)
    logger.info(
        "optional_components_loaded",
        auth=components.auth_components is not None,
        approvals=components.approval_service is not None,
    )
    return components


def register_auth_cqrs(runtime: Any, auth_components: Any) -> bool:
    """Register auth CQRS handlers on the runtime buses. Returns False when the
    auth module is not installed."""
    try:
        register_auth_command_handlers = _import_attribute(
            "mcp_hangar.auth.commands.handlers", "register_auth_command_handlers"
        )
        register_auth_query_handlers = _import_attribute(
            "mcp_hangar.auth.queries.handlers", "register_auth_query_handlers"
        )
    except ImportError:
        return False

    tap_store = getattr(auth_components, "tap_store", None)
    event_bus = getattr(runtime, "event_bus", None)

    register_auth_command_handlers(
        runtime.command_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
        tap_store=tap_store,
        event_bus=event_bus,
    )
    register_auth_query_handlers(
        runtime.query_bus,
        api_key_store=getattr(auth_components, "api_key_store", None),
        role_store=getattr(auth_components, "role_store", None),
        tap_store=tap_store,
    )
    return True


def get_component_api_routes() -> list[Any]:
    """Return Starlette routes contributed by the optional auth / approval modules."""
    from starlette.routing import Mount

    routes: list[Any] = []
    try:
        auth_routes = _import_attribute("mcp_hangar.auth.api.routes", "auth_routes")
        routes.append(Mount("/auth", routes=auth_routes))
    except ImportError:
        pass

    try:
        approval_routes = _import_attribute("mcp_hangar.approvals.api.routes", "approval_routes")
        routes.extend(cast(list[Any], approval_routes))
    except ImportError:
        pass

    return routes


def create_persistent_event_store(driver: str, config: dict[str, Any]) -> Any | None:
    """Build a persistent event store for the given driver, if supported."""
    if driver != "sqlite":
        return None

    sqlite_event_store = _import_attribute(
        "mcp_hangar.infrastructure.persistence.sqlite_event_store",
        "SQLiteEventStore",
    )
    db_path = config.get("path", "data/events.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite_event_store(db_path)


def create_observability_adapter(config: Any) -> ObservabilityPort | None:
    """Build the Langfuse observability adapter from config."""
    langfuse_config = _import_attribute("mcp_hangar.integrations.langfuse", "LangfuseConfig")
    adapter_type = _import_attribute("mcp_hangar.integrations.langfuse", "LangfuseObservabilityAdapter")

    adapter_config = langfuse_config(
        enabled=True,
        public_key=config.public_key,
        secret_key=config.secret_key,
        host=config.host,
        sample_rate=config.sample_rate,
        scrub_inputs=config.scrub_inputs,
        scrub_outputs=config.scrub_outputs,
    )
    return cast(ObservabilityPort, adapter_type(adapter_config))
