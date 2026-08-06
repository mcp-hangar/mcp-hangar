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
from ...logging_config import get_logger
from .composition import get_persistence_backend

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


def approvals_enabled(config: dict[str, Any]) -> bool:
    """Whether the approval gate service should be constructed.

    On by default. The service is inert until a policy actually gates a tool --
    :meth:`ToolAccessPolicy.requires_approval` decides that, and the executor
    returns before touching the gate when nothing does -- so constructing it
    always is what makes the gate *reachable* rather than conditional on a second
    switch nobody sets. Set ``approvals.enabled: false`` to opt out; the startup
    reachability check then refuses a config that gates a tool anyway.
    """
    approvals_config = config.get("approvals")
    if not isinstance(approvals_config, dict):
        return True
    return bool(approvals_config.get("enabled", True))


def _approval_repository_from_backend() -> Any:
    """The approval repository from the selected backend, or None.

    None means `bootstrap_approvals` builds its own, which is the compatibility
    path for a deployment that has not selected a backend.
    """
    return backend.approval_repository() if (backend := get_persistence_backend()) is not None else None


def build_approval_service(config: dict[str, Any], event_bus: Any = None) -> Any:
    """Construct the approval gate service, or return None when disabled.

    ``bootstrap_approvals()`` existed with **no call site anywhere in src/** --
    so ``ServerComponents.approval_service`` was never populated, the context's
    ``approval_gate`` was never set, and a tool on an ``approval_list`` executed
    immediately with a ``approval_gate_not_configured`` debug line (#678). It is
    called from here, the single loader both ``bootstrap()`` and every other
    entry point go through.
    """
    if not approvals_enabled(config):
        logger.info("approval_gate_disabled", reason="approvals.enabled=false")
        return None

    try:
        bootstrap_approvals = _import_attribute("mcp_hangar.approvals.bootstrap", "bootstrap_approvals")
        get_database = _import_attribute("mcp_hangar.infrastructure.persistence.database", "get_database")
    except ImportError:
        logger.warning("approval_gate_unavailable", reason="approvals_module_not_installed")
        return None

    try:
        return bootstrap_approvals(
            database=get_database(),
            event_bus=event_bus,
            config=config,
            repository=_approval_repository_from_backend(),
        )
    except Exception:  # noqa: BLE001 -- surfaced by the startup reachability check
        logger.error("approval_gate_bootstrap_failed", exc_info=True)
        return None


def load_components(
    config: dict[str, Any],
    event_bus: Any = None,
    event_publisher: Any = None,
    event_store: Any = None,
) -> ServerComponents:
    """Load the optional auth and approval components.

    Approvals are loaded independently of auth. They used to be loaded by
    nothing at all, and this function returned early -- before any approval
    wiring could run -- whenever auth was absent or disabled, which is the
    default (#678).

    Args:
        config: Full application configuration dictionary.
        event_bus: Optional event bus for auth / approval module wiring.
        event_publisher: Optional callable for publishing domain events.

    Returns:
        ServerComponents with the approval service and, when auth is enabled,
        the auth components.
    """
    approval_service = build_approval_service(config, event_bus=event_bus)

    exports = get_auth_compat_exports()
    if not exports.auth_available:
        logger.info("optional_components_unavailable", reason="auth_module_not_installed")
        return ServerComponents(approval_service=approval_service)

    auth_config = exports.parse_auth_config(config.get("auth"))
    if auth_config is None or not getattr(auth_config, "enabled", False):
        return ServerComponents(approval_service=approval_service)

    auth_components = exports.bootstrap_auth(
        auth_config,
        event_publisher=event_publisher,
        event_store=event_store,
        event_bus=event_bus,
        persistence_backend=get_persistence_backend(),
    )
    components = ServerComponents(auth_components=auth_components, approval_service=approval_service)
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


def attach_component_app_state(app: Any) -> None:
    """Publish the component services the REST routes read onto ``app.state``.

    Called from ``create_api_router`` so **every** REST surface gets the same
    wiring: the HTTP-serve path in ``server/lifecycle.py``, ``MCPServerFactory``,
    and any test client that builds the router directly. Setting it at one of
    those call sites only is how ``app.state.approval_gate_service`` came to be
    read by ``/api/approvals`` and set by nothing, answering 500 with an
    ``AttributeError`` (#678).

    The routes also fall back to the application context, so a router built
    before bootstrap populated it still resolves the service at request time.
    """
    from ..context import get_context

    try:
        approval_gate = getattr(get_context(), "approval_gate", None)
    except Exception:  # noqa: BLE001 -- no context (unit tests mounting routes directly) is not an error
        approval_gate = None

    app.state.approval_gate_service = approval_gate


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
