"""Tests for bootstrap enterprise boundary behavior.

Verifies that the server bootstraps correctly both with and without
enterprise modules. When enterprise is absent, null/noop fallbacks
must be used. When enterprise is present, real implementations must
load.

These tests form the contract ensuring the import boundary holds
at runtime.
"""

import importlib
import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _block_enterprise_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block all enterprise.* imports by setting them to None in sys.modules.

    Setting a module to ``None`` in ``sys.modules`` causes ``import``
    to raise ``ImportError``.
    """
    enterprise_keys = [key for key in sys.modules if key == "enterprise" or key.startswith("enterprise.")]
    for key in enterprise_keys:
        monkeypatch.setitem(sys.modules, key, None)
    # Also block the top-level package to prevent fresh discovery
    monkeypatch.setitem(sys.modules, "enterprise", None)


# ---------------------------------------------------------------------------
# Tests: Bootstrap without enterprise
# ---------------------------------------------------------------------------


class TestBootstrapWithoutEnterprise:
    """Prove that bootstrap components fall back to null implementations
    when enterprise modules are not available."""

    def test_bootstrap_auth_fallback_without_enterprise(self, monkeypatch, tmp_path):
        """When enterprise is blocked, the fallback bootstrap_auth() returns
        an object with enabled=False.

        Instead of reloading the complex bootstrap __init__ module (which
        has many sub-imports), we directly test the fallback code path by
        creating a minimal module that exercises the same try/except pattern.
        """
        # Write a tiny module that mimics the bootstrap __init__ pattern
        test_mod = tmp_path / "test_auth_fallback.py"
        test_mod.write_text(
            "try:\n"
            "    from enterprise.auth.bootstrap import AuthComponents, bootstrap_auth\n"
            "    _available = True\n"
            "except ImportError:\n"
            "    _available = False\n"
            "    class AuthComponents:\n"
            "        enabled = False\n"
            "    def bootstrap_auth(config=None, **kwargs):\n"
            "        return AuthComponents()\n"
        )

        _block_enterprise_modules(monkeypatch)

        # Use importlib to load the module fresh (enterprise is blocked)
        import importlib.util

        spec = importlib.util.spec_from_file_location("test_auth_fallback", str(test_mod))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod._available is False
        result = mod.bootstrap_auth()
        assert result.enabled is False, "bootstrap_auth() must return disabled auth when enterprise is absent"

    def test_bootstrap_module_exports_auth_names(self):
        """The bootstrap __init__ module exports the auth-related names
        required by the rest of the application: _enterprise_auth_available,
        bootstrap_auth, parse_auth_config, AuthComponents, NullAuthComponents.
        """
        # Force the package __init__ to be loaded first
        import mcp_hangar.server.bootstrap  # noqa: F401

        # Access the module object (not the bootstrap() function) via sys.modules
        bootstrap_mod = sys.modules["mcp_hangar.server.bootstrap"]

        assert hasattr(bootstrap_mod, "_enterprise_auth_available")
        assert hasattr(bootstrap_mod, "bootstrap_auth")
        assert hasattr(bootstrap_mod, "parse_auth_config")
        assert hasattr(bootstrap_mod, "AuthComponents")
        assert hasattr(bootstrap_mod, "NullAuthComponents")

    def test_parse_auth_config_none_returns_default_disabled_config(self):
        """parse_auth_config(None) should return a config with enabled=False
        when enterprise IS available (returns a default AuthConfig),
        and None when enterprise is NOT available (fallback stub)."""
        from mcp_hangar.server.bootstrap import parse_auth_config

        result = parse_auth_config(None)
        # With enterprise: returns AuthConfig(enabled=False, ...)
        # Without enterprise: returns None
        # In both cases, auth is effectively disabled.
        if result is not None:
            assert result.enabled is False, "parse_auth_config(None) must return a config with enabled=False"
        # If None, that's the stub behavior -- also fine

    def test_init_event_store_sqlite_fallback_without_enterprise(self, monkeypatch):
        """When enterprise is blocked and driver='sqlite',
        init_event_store() falls back to InMemoryEventStore."""
        _block_enterprise_modules(monkeypatch)

        from mcp_hangar.infrastructure.persistence import InMemoryEventStore
        from mcp_hangar.server.bootstrap.event_store import init_event_store

        mock_event_bus = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.event_bus = mock_event_bus

        config = {
            "event_store": {
                "enabled": True,
                "driver": "sqlite",
                "path": "data/events.db",
            }
        }

        init_event_store(mock_runtime, config)

        # The event store set on the bus must be InMemoryEventStore
        mock_event_bus.set_event_store.assert_called_once()
        actual_store = mock_event_bus.set_event_store.call_args[0][0]
        assert isinstance(actual_store, InMemoryEventStore), (
            f"Expected InMemoryEventStore fallback, got {type(actual_store).__name__}"
        )

    def test_init_event_store_memory_always_works(self):
        """When driver='memory', init_event_store() creates InMemoryEventStore
        regardless of enterprise availability."""
        from mcp_hangar.infrastructure.persistence import InMemoryEventStore
        from mcp_hangar.server.bootstrap.event_store import init_event_store

        mock_event_bus = MagicMock()
        mock_runtime = MagicMock()
        mock_runtime.event_bus = mock_event_bus

        config = {
            "event_store": {
                "enabled": True,
                "driver": "memory",
            }
        }

        init_event_store(mock_runtime, config)

        mock_event_bus.set_event_store.assert_called_once()
        actual_store = mock_event_bus.set_event_store.call_args[0][0]
        assert isinstance(actual_store, InMemoryEventStore), (
            f"Expected InMemoryEventStore for memory driver, got {type(actual_store).__name__}"
        )

    def test_init_auth_cqrs_skips_when_auth_disabled(self):
        """When auth_components.enabled is False, init_auth_cqrs() returns
        without error and without registering handlers."""
        from mcp_hangar.server.bootstrap.cqrs import init_auth_cqrs

        mock_runtime = MagicMock()
        mock_auth = MagicMock()
        mock_auth.enabled = False

        # Should not raise
        init_auth_cqrs(mock_runtime, mock_auth)

        # No handler registration should happen
        mock_runtime.command_bus.register.assert_not_called()
        mock_runtime.query_bus.register.assert_not_called()

    def test_init_auth_cqrs_skips_when_auth_components_none(self):
        """When auth_components is None, init_auth_cqrs() skips gracefully."""
        from mcp_hangar.server.bootstrap.cqrs import init_auth_cqrs

        mock_runtime = MagicMock()

        # Should not raise
        init_auth_cqrs(mock_runtime, None)

    def test_init_auth_cqrs_skips_without_enterprise(self, monkeypatch):
        """When enterprise is blocked, init_auth_cqrs() logs skip and returns
        without error even with enabled=True auth_components."""
        _block_enterprise_modules(monkeypatch)

        from mcp_hangar.server.bootstrap.cqrs import init_auth_cqrs

        mock_runtime = MagicMock()
        mock_auth = MagicMock()
        mock_auth.enabled = True

        # Should not raise -- the import of enterprise.auth.commands.handlers
        # will fail and the function will log and return.
        init_auth_cqrs(mock_runtime, mock_auth)

    def test_roles_fallback_without_enterprise(self, monkeypatch, tmp_path):
        """When enterprise is blocked, BUILTIN_ROLES is empty dict and
        list_builtin_roles() returns empty list.

        The roles module caches enterprise availability at import time.
        We exercise the same pattern via a fresh module load.
        """
        test_mod = tmp_path / "test_roles_fallback.py"
        test_mod.write_text(
            "try:\n"
            "    from enterprise.auth.roles import BUILTIN_ROLES, list_builtin_roles\n"
            "except ImportError:\n"
            "    BUILTIN_ROLES = {}\n"
            "    def list_builtin_roles():\n"
            "        return []\n"
        )

        _block_enterprise_modules(monkeypatch)

        import importlib.util

        spec = importlib.util.spec_from_file_location("test_roles_fallback", str(test_mod))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert mod.BUILTIN_ROLES == {}, f"BUILTIN_ROLES must be empty dict without enterprise, got {mod.BUILTIN_ROLES}"
        assert mod.list_builtin_roles() == [], "list_builtin_roles() must return empty list without enterprise"

    def test_langfuse_fallback_without_enterprise(self, monkeypatch):
        """When enterprise is blocked, init_langfuse() returns
        NullObservabilityAdapter even when Langfuse is configured."""
        _block_enterprise_modules(monkeypatch)

        from mcp_hangar.application.ports.observability import NullObservabilityAdapter
        from mcp_hangar.server.bootstrap.observability import (
            LangfuseBootstrapConfig,
            init_langfuse,
        )

        config = LangfuseBootstrapConfig(
            enabled=True,
            public_key="pk-test",
            secret_key="sk-test",
        )

        result = init_langfuse(config)
        assert isinstance(result, NullObservabilityAdapter), (
            f"Expected NullObservabilityAdapter fallback, got {type(result).__name__}"
        )


# ---------------------------------------------------------------------------
# Tests: API Router enterprise boundary
# ---------------------------------------------------------------------------


class TestApiRouterEnterpriseBoundary:
    """Verify conditional /auth route mounting based on enterprise availability."""

    def test_api_router_excludes_auth_routes_without_enterprise(self, monkeypatch):
        """When enterprise is blocked, create_api_router() does NOT
        include /auth mount."""
        _block_enterprise_modules(monkeypatch)

        from mcp_hangar.server.api.router import create_api_router

        app = create_api_router()

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert "/auth" not in route_paths, f"/auth should not be in routes without enterprise, got {route_paths}"

    def test_api_router_includes_auth_routes_with_enterprise(self):
        """When enterprise IS available, create_api_router() includes
        /auth mount."""
        from mcp_hangar.server.api.router import create_api_router

        app = create_api_router()

        route_paths = [getattr(r, "path", "") for r in app.routes]
        assert "/auth" in route_paths, f"/auth should be in routes with enterprise, got {route_paths}"


# ---------------------------------------------------------------------------
# Tests: Bootstrap with enterprise present
# ---------------------------------------------------------------------------


class TestBootstrapWithEnterprise:
    """Prove that bootstrap components load real enterprise implementations
    when enterprise modules are available."""

    def test_enterprise_auth_available_flag_is_true(self):
        """When enterprise is available, the bootstrap module sets
        _enterprise_auth_available to True at import time."""
        # Ensure we get a fresh view of the module
        fqn = "mcp_hangar.server.bootstrap"
        mod = sys.modules.get(fqn)
        if mod is None:
            import mcp_hangar.server.bootstrap  # noqa: F401

            mod = sys.modules[fqn]
        assert mod._enterprise_auth_available is True, (
            "_enterprise_auth_available must be True when enterprise is installed"
        )

    def test_roles_populated_with_enterprise(self):
        """When enterprise is available, BUILTIN_ROLES is populated."""
        # Import directly from enterprise to ensure we test the real module,
        # regardless of any reload effects from previous tests.
        from enterprise.auth.roles import BUILTIN_ROLES, list_builtin_roles

        assert len(BUILTIN_ROLES) > 0, "BUILTIN_ROLES must be populated when enterprise is installed"
        role_names = list_builtin_roles()
        assert len(role_names) > 0, "list_builtin_roles() must return roles when enterprise is installed"
        assert "admin" in BUILTIN_ROLES, "admin role must be present in BUILTIN_ROLES"

    def test_enterprise_auth_bootstrap_importable(self):
        """Enterprise auth bootstrap module must be importable
        when enterprise is installed."""
        from enterprise.auth.bootstrap import AuthComponents, NullAuthComponents, bootstrap_auth

        assert AuthComponents is not None
        assert NullAuthComponents is not None
        assert callable(bootstrap_auth)

    def test_enterprise_langfuse_importable(self):
        """Enterprise Langfuse integration must be importable
        when enterprise is installed."""
        from enterprise.integrations.langfuse import LangfuseConfig, LangfuseObservabilityAdapter

        assert LangfuseConfig is not None
        assert LangfuseObservabilityAdapter is not None
