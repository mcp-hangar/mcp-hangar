"""Tests for fastmcp_server.py module.

Tests cover the MCPServerFactory and builder pattern.
"""

from unittest.mock import AsyncMock, Mock

import pytest

from mcp_hangar.fastmcp_server import HangarFunctions, MCPServerFactory, MCPServerFactoryBuilder, ServerConfig


@pytest.fixture
def mock_registry():
    """Create mock registry functions."""
    return HangarFunctions(
        list=Mock(return_value={"providers": []}),
        start=Mock(return_value={"status": "started"}),
        stop=Mock(return_value={"status": "stopped"}),
        invoke=Mock(return_value={"result": 42}),
        tools=Mock(return_value={"tools": []}),
        details=Mock(return_value={"provider": "test"}),
        health=Mock(return_value={"status": "healthy"}),
    )


@pytest.fixture
def mock_registry_with_discovery():
    """Create mock registry functions with discovery."""
    return HangarFunctions(
        list=Mock(return_value={"providers": []}),
        start=Mock(return_value={"status": "started"}),
        stop=Mock(return_value={"status": "stopped"}),
        invoke=Mock(return_value={"result": 42}),
        tools=Mock(return_value={"tools": []}),
        details=Mock(return_value={"provider": "test"}),
        health=Mock(return_value={"status": "healthy"}),
        discover=AsyncMock(return_value={"discovered": 0}),
        discovered=Mock(return_value={"pending": []}),
        quarantine=Mock(return_value={"quarantined": []}),
        approve=AsyncMock(return_value={"approved": "test"}),
        sources=Mock(return_value={"sources": []}),
        metrics=Mock(return_value={"metrics": {}}),
    )


class TestHangarFunctions:
    """Tests for HangarFunctions dataclass."""

    def test_registry_functions_immutable(self, mock_registry):
        """HangarFunctions is frozen dataclass."""
        with pytest.raises(Exception):  # FrozenInstanceError
            mock_registry.list = Mock()

    def test_discovery_functions_optional(self):
        """Discovery functions can be None."""
        registry = HangarFunctions(
            list=Mock(),
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(),
            # No discovery functions
        )

        assert registry.discover is None
        assert registry.discovered is None
        assert registry.quarantine is None
        assert registry.approve is None
        assert registry.sources is None
        assert registry.metrics is None

    def test_registry_functions_with_all_fields(self, mock_registry_with_discovery):
        """HangarFunctions accepts all fields."""
        assert mock_registry_with_discovery.list is not None
        assert mock_registry_with_discovery.discover is not None
        assert mock_registry_with_discovery.sources is not None


class TestServerConfig:
    """Tests for ServerConfig dataclass."""

    def test_server_config_defaults(self):
        """ServerConfig has sensible defaults."""
        config = ServerConfig()

        assert config.host == "0.0.0.0"
        assert config.port == 8000
        assert config.streamable_http_path == "/mcp"
        assert config.sse_path == "/sse"
        assert config.message_path == "/messages/"

    def test_server_config_custom_values(self):
        """ServerConfig accepts custom values."""
        config = ServerConfig(
            host="localhost",
            port=9000,
            streamable_http_path="/custom-mcp",
            sse_path="/custom-sse",
            message_path="/custom-messages/",
        )

        assert config.host == "localhost"
        assert config.port == 9000
        assert config.streamable_http_path == "/custom-mcp"

    def test_server_config_immutable(self):
        """ServerConfig is frozen dataclass."""
        config = ServerConfig()

        with pytest.raises(Exception):  # FrozenInstanceError
            config.port = 9000


class TestMCPServerFactory:
    """Tests for MCPServerFactory class."""

    def test_create_server_returns_fastmcp(self, mock_registry):
        """create_server() returns FastMCP instance."""
        factory = MCPServerFactory(mock_registry)
        server = factory.create_server()

        assert server is not None
        assert server.name == "mcp-hangar"

    def test_create_server_caches_instance(self, mock_registry):
        """Repeated calls return same instance."""
        factory = MCPServerFactory(mock_registry)
        server1 = factory.create_server()
        server2 = factory.create_server()

        assert server1 is server2

    def test_separate_factories_create_separate_servers(self, mock_registry):
        """Different factories create different servers."""
        factory1 = MCPServerFactory(mock_registry)
        factory2 = MCPServerFactory(mock_registry)

        server1 = factory1.create_server()
        server2 = factory2.create_server()

        assert server1 is not server2

    def test_config_applied_to_server(self, mock_registry):
        """ServerConfig values are applied."""
        config = ServerConfig(host="127.0.0.1", port=9999)
        factory = MCPServerFactory(mock_registry, config)
        server = factory.create_server()

        assert server.settings.host == "127.0.0.1"
        assert server.settings.port == 9999

    def test_default_config_used_when_none(self, mock_registry):
        """Default ServerConfig used when not provided."""
        factory = MCPServerFactory(mock_registry)

        assert factory.config.host == "0.0.0.0"
        assert factory.config.port == 8000

    def test_registry_property(self, mock_registry):
        """registry property returns HangarFunctions."""
        factory = MCPServerFactory(mock_registry)

        assert factory.hangar is mock_registry

    def test_config_property(self, mock_registry):
        """config property returns ServerConfig."""
        config = ServerConfig(port=3000)
        factory = MCPServerFactory(mock_registry, config)

        assert factory.config is config

    def test_create_asgi_app_returns_callable(self, mock_registry):
        """create_asgi_app() returns ASGI app."""
        factory = MCPServerFactory(mock_registry)
        app = factory.create_asgi_app()

        assert callable(app)

    def test_builder_method_returns_builder(self, mock_registry):
        """builder() class method returns MCPServerFactoryBuilder."""
        builder = MCPServerFactory.builder()

        assert isinstance(builder, MCPServerFactoryBuilder)


class TestMCPServerFactoryReadinessChecks:
    """Tests for readiness check functionality."""

    def test_readiness_checks_call_registry(self, mock_registry):
        """Readiness checks call list and health."""
        factory = MCPServerFactory(mock_registry)
        checks = factory._run_readiness_checks()

        mock_registry.list.assert_called_once()
        mock_registry.health.assert_called_once()
        assert checks["hangar_list_ok"] is True
        assert checks["hangar_health_ok"] is True
        assert checks["hangar_wired"] is True

    def test_readiness_checks_handle_list_error(self):
        """Readiness checks handle list() exception."""
        registry = HangarFunctions(
            list=Mock(side_effect=RuntimeError("list error")),
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(return_value={"status": "ok"}),
        )
        factory = MCPServerFactory(registry)
        checks = factory._run_readiness_checks()

        assert checks["hangar_list_ok"] is False
        assert "list error" in checks["hangar_list_error"]
        assert checks["hangar_health_ok"] is True

    def test_readiness_checks_handle_health_error(self):
        """Readiness checks handle health() exception."""
        registry = HangarFunctions(
            list=Mock(return_value={"providers": []}),
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(side_effect=RuntimeError("health error")),
        )
        factory = MCPServerFactory(registry)
        checks = factory._run_readiness_checks()

        assert checks["hangar_list_ok"] is True
        assert checks["hangar_health_ok"] is False
        assert "health error" in checks["hangar_health_error"]

    def test_readiness_checks_invalid_list_response(self):
        """Readiness checks detect invalid list response."""
        registry = HangarFunctions(
            list=Mock(return_value={"invalid": "response"}),  # Missing "providers"
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(return_value={"status": "ok"}),
        )
        factory = MCPServerFactory(registry)
        checks = factory._run_readiness_checks()

        assert checks["hangar_list_ok"] is False

    def test_readiness_checks_invalid_health_response(self):
        """Readiness checks detect invalid health response."""
        registry = HangarFunctions(
            list=Mock(return_value={"providers": []}),
            start=Mock(),
            stop=Mock(),
            invoke=Mock(),
            tools=Mock(),
            details=Mock(),
            health=Mock(return_value={"invalid": "response"}),  # Missing "status"
        )
        factory = MCPServerFactory(registry)
        checks = factory._run_readiness_checks()

        assert checks["hangar_health_ok"] is False


class TestMCPServerFactoryMetrics:
    """Tests for metrics update functionality."""

    def test_update_metrics_calls_list(self, mock_registry):
        """_update_metrics calls registry.list()."""
        factory = MCPServerFactory(mock_registry)

        with pytest.MonkeyPatch().context() as m:
            mock_update = Mock()
            m.setattr("mcp_hangar.metrics.update_provider_state", mock_update)

            factory._update_metrics()

        mock_registry.list.assert_called()

    def test_update_metrics_handles_error(self, mock_registry):
        """_update_metrics handles exceptions gracefully."""
        mock_registry.list.side_effect = RuntimeError("error")
        factory = MCPServerFactory(mock_registry)

        # Should not raise
        factory._update_metrics()


class TestMCPServerFactoryBuilder:
    """Tests for MCPServerFactoryBuilder class."""

    def test_builder_creates_factory(self, mock_registry):
        """Builder creates working factory."""
        factory = (
            MCPServerFactory.builder()
            .with_hangar(
                mock_registry.list,
                mock_registry.start,
                mock_registry.stop,
                mock_registry.invoke,
                mock_registry.tools,
                mock_registry.details,
                mock_registry.health,
            )
            .build()
        )

        assert factory is not None
        assert factory.create_server() is not None

    def test_builder_requires_all_core_functions(self):
        """Builder raises if core functions missing."""
        with pytest.raises(ValueError, match="core hangar functions"):
            MCPServerFactory.builder().build()

    def test_builder_requires_all_core_functions_partial(self):
        """Builder raises if some core functions missing."""
        with pytest.raises(ValueError, match="core hangar functions"):
            MCPServerFactory.builder().with_hangar(
                Mock(),
                Mock(),
                Mock(),
                Mock(),
                Mock(),
                Mock(),
                None,  # Missing health
            ).build()

    def test_builder_with_discovery(self, mock_registry):
        """Builder accepts discovery functions."""
        mock_discover = AsyncMock(return_value={"discovered": 0})

        factory = (
            MCPServerFactory.builder()
            .with_hangar(
                mock_registry.list,
                mock_registry.start,
                mock_registry.stop,
                mock_registry.invoke,
                mock_registry.tools,
                mock_registry.details,
                mock_registry.health,
            )
            .with_discovery(discover_fn=mock_discover)
            .build()
        )

        assert factory is not None
        assert factory.hangar.discover is mock_discover

    def test_builder_with_all_discovery_functions(self, mock_registry):
        """Builder accepts all discovery functions."""
        factory = (
            MCPServerFactory.builder()
            .with_hangar(
                mock_registry.list,
                mock_registry.start,
                mock_registry.stop,
                mock_registry.invoke,
                mock_registry.tools,
                mock_registry.details,
                mock_registry.health,
            )
            .with_discovery(
                discover_fn=AsyncMock(),
                discovered_fn=Mock(),
                quarantine_fn=Mock(),
                approve_fn=AsyncMock(),
                sources_fn=Mock(),
                metrics_fn=Mock(),
            )
            .build()
        )

        assert factory.hangar.discover is not None
        assert factory.hangar.discovered is not None
        assert factory.hangar.quarantine is not None
        assert factory.hangar.approve is not None
        assert factory.hangar.sources is not None
        assert factory.hangar.metrics is not None

    def test_builder_with_config(self, mock_registry):
        """Builder accepts server config."""
        factory = (
            MCPServerFactory.builder()
            .with_hangar(
                mock_registry.list,
                mock_registry.start,
                mock_registry.stop,
                mock_registry.invoke,
                mock_registry.tools,
                mock_registry.details,
                mock_registry.health,
            )
            .with_config(host="localhost", port=3000)
            .build()
        )

        server = factory.create_server()
        assert server.settings.port == 3000
        assert server.settings.host == "localhost"

    def test_builder_with_all_config_options(self, mock_registry):
        """Builder accepts all config options."""
        factory = (
            MCPServerFactory.builder()
            .with_hangar(
                mock_registry.list,
                mock_registry.start,
                mock_registry.stop,
                mock_registry.invoke,
                mock_registry.tools,
                mock_registry.details,
                mock_registry.health,
            )
            .with_config(
                host="127.0.0.1",
                port=9000,
                streamable_http_path="/custom-mcp",
                sse_path="/custom-sse",
                message_path="/custom-messages/",
            )
            .build()
        )

        assert factory.config.host == "127.0.0.1"
        assert factory.config.port == 9000
        assert factory.config.streamable_http_path == "/custom-mcp"
        assert factory.config.sse_path == "/custom-sse"
        assert factory.config.message_path == "/custom-messages/"

    def test_builder_chaining(self, mock_registry):
        """Builder methods return self for chaining."""
        builder = MCPServerFactory.builder()

        result1 = builder.with_hangar(
            mock_registry.list,
            mock_registry.start,
            mock_registry.stop,
            mock_registry.invoke,
            mock_registry.tools,
            mock_registry.details,
            mock_registry.health,
        )
        result2 = result1.with_discovery()
        result3 = result2.with_config()

        assert result1 is builder
        assert result2 is builder
        assert result3 is builder


class TestMultipleInstances:
    """Tests verifying multiple independent instances."""

    def test_multiple_factories_independent(self, mock_registry):
        """Multiple factories maintain independent state."""
        factory1 = MCPServerFactory(mock_registry)
        factory2 = MCPServerFactory(mock_registry, ServerConfig(port=9000))

        server1 = factory1.create_server()
        server2 = factory2.create_server()

        assert server1.settings.port == 8000
        assert server2.settings.port == 9000

    def test_factories_dont_share_mcp_instance(self, mock_registry):
        """Each factory caches its own MCP instance."""
        factory1 = MCPServerFactory(mock_registry)
        factory2 = MCPServerFactory(mock_registry)

        server1a = factory1.create_server()
        server1b = factory1.create_server()
        server2a = factory2.create_server()
        server2b = factory2.create_server()

        # Same factory returns same instance
        assert server1a is server1b
        assert server2a is server2b

        # Different factories return different instances
        assert server1a is not server2a


class TestNoSideEffectsOnImport:
    """Tests verifying no side effects on module import."""

    def test_import_doesnt_create_server(self):
        """Importing module doesn't create any servers."""
        # If this test runs, import already happened without error
        import mcp_hangar.fastmcp_server as mod

        # Verify module exports exist
        assert hasattr(mod, "MCPServerFactory")
        assert hasattr(mod, "HangarFunctions")

    def test_import_doesnt_call_hangar_functions(self, mock_registry):
        """Importing and creating factory doesn't call registry functions."""
        _factory = MCPServerFactory(mock_registry)  # noqa: F841

        # Registry functions should not be called just by creating factory
        mock_registry.list.assert_not_called()
        mock_registry.start.assert_not_called()
        mock_registry.health.assert_not_called()

    def test_create_server_doesnt_call_hangar(self, mock_registry):
        """Creating server doesn't call hangar functions."""
        factory = MCPServerFactory(mock_registry)
        factory.create_server()

        # Registry functions should not be called just by creating server
        mock_registry.list.assert_not_called()
        mock_registry.start.assert_not_called()
        mock_registry.health.assert_not_called()
