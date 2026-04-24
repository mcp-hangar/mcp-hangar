"""Core enterprise boundary and provider registry.

This module is the only core-side boundary that knows how to reach optional
enterprise functionality. Core bootstrap/router code imports helpers from here
instead of importing ``enterprise.*`` modules directly.

Supported hook names for provider implementations:

- ``load_components``: bootstrap enterprise auth/approval components
- ``validate_license_key``: resolve a ``LicenseTier`` from a raw key
- ``register_auth_cqrs``: register auth command/query handlers
- ``extend_api_routes``: contribute Starlette routes/mounts
- ``create_event_store``: build enterprise-backed event stores
- ``create_observability_adapter``: build enterprise observability adapters
- ``auth_compat_exports``: provide legacy bootstrap auth exports

Entry points remain supported, but when no entry points are registered the core
falls back to a local provider that loads ``enterprise`` modules through
``importlib`` so the monorepo layout continues to work.
"""

# pyright: reportExplicitAny=false, reportAny=false, reportMissingTypeArgument=false, reportUnknownParameterType=false, reportUnknownVariableType=false

from __future__ import annotations

import importlib
import importlib.metadata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from collections.abc import Callable

from ...application.ports.observability import ObservabilityPort
from ...domain.value_objects.license import LicenseTier
from ...infrastructure.event_store import get_event_store
from ...logging_config import get_logger

logger = get_logger(__name__)
ENTERPRISE_ENTRY_POINT_GROUP = "mcp_hangar.enterprise"


@dataclass(frozen=True)
class LicenseValidation:
    """Core-friendly license validation result."""

    tier: LicenseTier = LicenseTier.COMMUNITY
    org: str = ""
    grace_period: bool = False
    error: str | None = None


class _FallbackAuthComponents:
    """Stub AuthComponents used when enterprise auth is unavailable."""

    enabled: bool = False
    api_key_store: Any = None
    role_store: Any = None
    tap_store: Any = None
    authn_middleware: Any = None
    authz_middleware: Any = None


class _FallbackNullAuthComponents(_FallbackAuthComponents):
    """Null/noop auth implementation used when enterprise is unavailable."""


def _fallback_bootstrap_auth(_config: Any = None, **_kwargs: Any) -> _FallbackNullAuthComponents:
    """Return noop auth components when enterprise is not installed."""
    return _FallbackNullAuthComponents()


def _fallback_parse_auth_config(_raw: dict[str, Any] | None = None) -> None:
    """Return empty config when enterprise is not installed."""
    return None


@dataclass(frozen=True)
class AuthCompatibilityExports:
    """Legacy auth exports re-exposed from ``mcp_hangar.server.bootstrap``."""

    AuthComponents: type[Any]
    NullAuthComponents: type[Any]
    bootstrap_auth: Callable[..., Any]
    parse_auth_config: Callable[[dict[str, Any] | None], Any]
    enterprise_auth_available: bool


@dataclass(frozen=True)
class EnterpriseProvider:
    """Provider hooks for optional enterprise integrations."""

    name: str
    load_components: Callable[[LicenseTier, dict[str, Any], Any, Any], EnterpriseComponents] | None = None
    validate_license_key: Callable[[str | None], LicenseValidation | Any | None] | None = None
    register_auth_cqrs: Callable[[Any, Any], bool] | None = None
    extend_api_routes: Callable[[], list[Any]] | None = None
    create_event_store: Callable[[str, dict[str, Any]], Any | None] | None = None
    create_observability_adapter: Callable[[Any], ObservabilityPort | None] | None = None
    auth_compat_exports: Callable[[], AuthCompatibilityExports] | None = None


_REGISTERED_ENTERPRISE_PROVIDERS: dict[str, EnterpriseProvider] = {}


@dataclass
class EnterpriseComponents:
    """Container for all enterprise module instances loaded based on license tier."""

    license_tier: LicenseTier = LicenseTier.COMMUNITY
    auth_components: Any = None
    approval_service: Any = None


def _merge_enterprise_components(base: EnterpriseComponents, loaded: EnterpriseComponents) -> None:
    """Merge plugin-provided enterprise components into the result container."""
    if loaded.auth_components is not None:
        base.auth_components = loaded.auth_components
    if loaded.approval_service is not None:
        base.approval_service = loaded.approval_service


def register_enterprise_provider(provider: EnterpriseProvider) -> None:
    """Register a core-visible enterprise provider."""
    _REGISTERED_ENTERPRISE_PROVIDERS[provider.name] = provider


def get_registered_enterprise_providers() -> tuple[EnterpriseProvider, ...]:
    """Return registered providers in deterministic insertion order."""
    return tuple(_REGISTERED_ENTERPRISE_PROVIDERS.values())


def _import_attribute(module_name: str, attribute_name: str) -> Any:
    module = importlib.import_module(module_name)
    return getattr(module, attribute_name)


def get_auth_compat_exports() -> AuthCompatibilityExports:
    """Resolve legacy auth compatibility exports from enterprise or fallback."""
    try:
        auth_components = _import_attribute("enterprise.auth.bootstrap", "AuthComponents")
        null_auth_components = _import_attribute("enterprise.auth.bootstrap", "NullAuthComponents")
        bootstrap_auth = _import_attribute("enterprise.auth.bootstrap", "bootstrap_auth")
        parse_auth_config = _import_attribute("enterprise.auth.config", "parse_auth_config")
    except ImportError:
        return AuthCompatibilityExports(
            AuthComponents=_FallbackAuthComponents,
            NullAuthComponents=_FallbackNullAuthComponents,
            bootstrap_auth=_fallback_bootstrap_auth,
            parse_auth_config=_fallback_parse_auth_config,
            enterprise_auth_available=False,
        )

    return AuthCompatibilityExports(
        AuthComponents=cast(type[Any], auth_components),
        NullAuthComponents=cast(type[Any], null_auth_components),
        bootstrap_auth=cast(Callable[..., Any], bootstrap_auth),
        parse_auth_config=cast(Callable[[dict[str, Any] | None], Any], parse_auth_config),
        enterprise_auth_available=True,
    )


def _builtin_load_components(
    tier: LicenseTier,
    config: dict[str, Any],
    event_bus: Any = None,
    event_publisher: Any = None,
) -> EnterpriseComponents:
    exports = get_auth_compat_exports()
    if not exports.enterprise_auth_available:
        return EnterpriseComponents(license_tier=tier)

    auth_config = exports.parse_auth_config(config.get("auth"))
    if auth_config is None or not getattr(auth_config, "enabled", False):
        return EnterpriseComponents(license_tier=tier)

    auth_components = exports.bootstrap_auth(
        auth_config,
        event_publisher=event_publisher,
        event_store=get_event_store(),
        event_bus=event_bus,
    )
    return EnterpriseComponents(license_tier=tier, auth_components=auth_components)


def _builtin_validate_license_key(raw_license_key: str | None) -> LicenseValidation:
    try:
        license_validator = _import_attribute("enterprise.auth.license", "LicenseValidator")
    except ImportError:
        return LicenseValidation()

    result = license_validator().validate(raw_license_key)
    return LicenseValidation(
        tier=getattr(result, "tier", LicenseTier.COMMUNITY),
        org=getattr(result, "org", ""),
        grace_period=bool(getattr(result, "grace_period", False)),
        error=getattr(result, "error", None),
    )


def _builtin_register_auth_cqrs(runtime: Any, auth_components: Any) -> bool:
    try:
        register_auth_command_handlers = _import_attribute(
            "enterprise.auth.commands.handlers", "register_auth_command_handlers"
        )
        register_auth_query_handlers = _import_attribute(
            "enterprise.auth.queries.handlers", "register_auth_query_handlers"
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


def _builtin_extend_api_routes() -> list[Any]:
    from starlette.routing import Mount

    routes: list[Any] = []
    try:
        auth_routes = _import_attribute("enterprise.auth.api.routes", "auth_routes")
        routes.append(Mount("/auth", routes=auth_routes))
    except ImportError:
        pass

    try:
        approval_routes = _import_attribute("enterprise.approvals.api.routes", "approval_routes")
        routes.extend(cast(list[Any], approval_routes))
    except ImportError:
        pass

    return routes


def _builtin_create_event_store(driver: str, config: dict[str, Any]) -> Any | None:
    if driver != "sqlite":
        return None

    sqlite_event_store = _import_attribute("enterprise.persistence.sqlite_event_store", "SQLiteEventStore")
    db_path = config.get("path", "data/events.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite_event_store(db_path)


def _builtin_create_observability_adapter(config: Any) -> ObservabilityPort | None:
    langfuse_config = _import_attribute("enterprise.integrations.langfuse", "LangfuseConfig")
    adapter_type = _import_attribute("enterprise.integrations.langfuse", "LangfuseObservabilityAdapter")

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


def _builtin_auth_compat_exports() -> AuthCompatibilityExports:
    return get_auth_compat_exports()


def _get_builtin_enterprise_provider() -> EnterpriseProvider | None:
    try:
        _ = importlib.import_module("enterprise")
    except ImportError:
        return None

    return EnterpriseProvider(
        name="builtin-enterprise",
        load_components=_builtin_load_components,
        validate_license_key=_builtin_validate_license_key,
        register_auth_cqrs=_builtin_register_auth_cqrs,
        extend_api_routes=_builtin_extend_api_routes,
        create_event_store=_builtin_create_event_store,
        create_observability_adapter=_builtin_create_observability_adapter,
        auth_compat_exports=_builtin_auth_compat_exports,
    )


def validate_license_key(raw_license_key: str | None) -> LicenseValidation:
    """Validate a license key through the registered enterprise boundary."""
    for provider in get_registered_enterprise_providers():
        if provider.validate_license_key is None:
            continue
        result = provider.validate_license_key(raw_license_key)
        if result is not None:
            return cast(LicenseValidation, result)

    builtin_provider = _get_builtin_enterprise_provider()
    if builtin_provider is not None and builtin_provider.validate_license_key is not None:
        result = builtin_provider.validate_license_key(raw_license_key)
        if result is not None:
            return cast(LicenseValidation, result)

    return LicenseValidation()


def register_auth_cqrs(runtime: Any, auth_components: Any) -> bool:
    """Register enterprise auth CQRS handlers through the provider boundary."""
    for provider in get_registered_enterprise_providers():
        if provider.register_auth_cqrs is not None and provider.register_auth_cqrs(runtime, auth_components):
            return True

    builtin_provider = _get_builtin_enterprise_provider()
    if builtin_provider is not None and builtin_provider.register_auth_cqrs is not None:
        return builtin_provider.register_auth_cqrs(runtime, auth_components)

    return False


def get_enterprise_api_routes() -> list[Any]:
    """Return Starlette routes contributed by enterprise providers."""
    routes: list[Any] = []
    for provider in get_registered_enterprise_providers():
        if provider.extend_api_routes is not None:
            routes.extend(provider.extend_api_routes())

    if routes:
        return routes

    builtin_provider = _get_builtin_enterprise_provider()
    if builtin_provider is not None and builtin_provider.extend_api_routes is not None:
        return builtin_provider.extend_api_routes()

    return []


def create_enterprise_event_store(driver: str, config: dict[str, Any]) -> Any | None:
    """Ask enterprise providers to create an event store for the given driver."""
    for provider in get_registered_enterprise_providers():
        if provider.create_event_store is None:
            continue
        event_store = provider.create_event_store(driver, config)
        if event_store is not None:
            return event_store

    builtin_provider = _get_builtin_enterprise_provider()
    if builtin_provider is not None and builtin_provider.create_event_store is not None:
        return builtin_provider.create_event_store(driver, config)

    return None


def create_enterprise_observability_adapter(config: Any) -> ObservabilityPort | None:
    """Ask enterprise providers to create an observability adapter."""
    for provider in get_registered_enterprise_providers():
        if provider.create_observability_adapter is None:
            continue
        adapter = provider.create_observability_adapter(config)
        if adapter is not None:
            return adapter

    builtin_provider = _get_builtin_enterprise_provider()
    if builtin_provider is not None and builtin_provider.create_observability_adapter is not None:
        return builtin_provider.create_observability_adapter(config)

    return None


def load_enterprise_modules(
    tier: LicenseTier,
    config: dict[str, Any],
    event_bus: Any = None,
    event_publisher: Any = None,
) -> EnterpriseComponents:
    """Load enterprise modules based on the validated license tier.

    For COMMUNITY tier, no enterprise imports are attempted.  For PRO and
    ENTERPRISE tiers, auth modules are loaded when available.

    Args:
        tier: Validated license tier from LicenseValidator.
        config: Full application configuration dictionary.
        event_bus: Optional event bus for enterprise module wiring.
        event_publisher: Optional callable for publishing domain events.

    Returns:
        EnterpriseComponents with populated fields for loaded modules.
    """
    components = EnterpriseComponents(license_tier=tier)

    if tier == LicenseTier.COMMUNITY:
        logger.info("enterprise_modules_skipped", tier="community")
        return components

    entry_points = tuple(importlib.metadata.entry_points(group=ENTERPRISE_ENTRY_POINT_GROUP))

    provider_loaders: list[tuple[str, Callable[[LicenseTier, dict[str, Any], Any, Any], EnterpriseComponents]]] = []
    if entry_points:
        for entry_point in entry_points:
            loader_fn = cast(
                Callable[[LicenseTier, dict[str, Any], Any, Any], EnterpriseComponents],
                entry_point.load(),
            )
            provider_loaders.append((entry_point.name, loader_fn))
    else:
        builtin_provider = _get_builtin_enterprise_provider()
        if builtin_provider is None or builtin_provider.load_components is None:
            logger.info("enterprise_modules_unavailable", tier=tier.value, reason="no_entry_points_registered")
            return components
        provider_loaders.append((builtin_provider.name, builtin_provider.load_components))

    for provider_name, loader in provider_loaders:
        loaded_components = loader(tier, config, event_bus, event_publisher)
        if not isinstance(loaded_components, EnterpriseComponents):
            msg = (
                f"Enterprise loader '{provider_name}' returned {type(loaded_components).__name__}; "
                "expected EnterpriseComponents"
            )
            raise TypeError(msg)
        _merge_enterprise_components(components, loaded_components)

    loaded_flags = {
        "auth": components.auth_components is not None,
        "approvals": components.approval_service is not None,
    }
    logger.info(
        "enterprise_modules_loaded",
        tier=tier.value,
        entry_points=[provider_name for provider_name, _ in provider_loaders],
        **loaded_flags,
    )

    return components
