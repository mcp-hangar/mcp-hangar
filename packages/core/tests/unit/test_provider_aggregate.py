"""Tests for Provider aggregate root."""

import threading

from mcp_hangar.domain.events import ProviderStopped
from mcp_hangar.domain.model import Provider, ProviderState
from mcp_hangar.domain.model.provider import VALID_TRANSITIONS
from mcp_hangar.domain.value_objects import ProviderMode


class TestProviderInitialization:
    """Test Provider initialization."""

    def test_create_subprocess_provider(self):
        """Test creating a subprocess provider."""
        provider = Provider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        assert provider.provider_id == "test-provider"
        assert provider.mode == ProviderMode.SUBPROCESS
        assert provider.state == ProviderState.COLD

    def test_create_docker_provider(self):
        """Test creating a docker provider."""
        provider = Provider(provider_id="docker-provider", mode="docker", image="test:latest")

        assert provider.provider_id == "docker-provider"
        assert provider.mode == ProviderMode.DOCKER

    def test_provider_initial_state(self):
        """Test provider initial state."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        assert provider.state == ProviderState.COLD
        assert provider.is_alive is False
        assert provider.last_used == 0.0
        assert provider.tools.count() == 0
        assert provider.version == 0

    def test_provider_with_env_vars(self):
        """Test provider with environment variables."""
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
            env={"KEY": "value"},
        )

        assert provider._env == {"KEY": "value"}

    def test_provider_with_custom_config(self):
        """Test provider with custom configuration."""
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
            idle_ttl_s=600,
            health_check_interval_s=120,
            max_consecutive_failures=5,
        )

        assert provider._idle_ttl.seconds == 600
        assert provider._health_check_interval.seconds == 120
        assert provider.health.max_consecutive_failures == 5


class TestProviderStateTransitions:
    """Test provider state transitions."""

    def test_valid_transitions(self):
        """Test valid state transitions are defined."""
        # COLD can transition to INITIALIZING
        assert ProviderState.INITIALIZING in VALID_TRANSITIONS[ProviderState.COLD]

        # INITIALIZING can transition to READY, DEAD, or DEGRADED
        assert ProviderState.READY in VALID_TRANSITIONS[ProviderState.INITIALIZING]
        assert ProviderState.DEAD in VALID_TRANSITIONS[ProviderState.INITIALIZING]
        assert ProviderState.DEGRADED in VALID_TRANSITIONS[ProviderState.INITIALIZING]

        # READY can transition to COLD, DEAD, or DEGRADED
        assert ProviderState.COLD in VALID_TRANSITIONS[ProviderState.READY]
        assert ProviderState.DEAD in VALID_TRANSITIONS[ProviderState.READY]
        assert ProviderState.DEGRADED in VALID_TRANSITIONS[ProviderState.READY]


class TestAggregateRoot:
    """Test AggregateRoot base functionality."""

    def test_record_event(self):
        """Test recording domain events."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # Provider records events during operations
        assert provider.has_uncommitted_events() is False

    def test_collect_events_clears_list(self):
        """Test that collecting events clears the internal list."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # Manually record an event for testing
        from mcp_hangar.domain.events import ProviderStopped

        provider._record_event(ProviderStopped(provider_id="test", reason="test"))

        assert provider.has_uncommitted_events() is True

        events = provider.collect_events()
        assert len(events) == 1
        assert provider.has_uncommitted_events() is False

    def test_version_tracking(self):
        """Test version is tracked correctly."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        initial_version = provider.version
        provider._increment_version()

        assert provider.version == initial_version + 1


class TestProviderProperties:
    """Test Provider properties."""

    def test_provider_id_property(self):
        """Test provider_id property returns string."""
        provider = Provider(provider_id="test-provider", mode="subprocess", command=["test"])

        assert provider.provider_id == "test-provider"
        assert isinstance(provider.provider_id, str)

    def test_id_property_returns_value_object(self):
        """Test id property returns ProviderId value object."""
        from mcp_hangar.domain.value_objects import ProviderId

        provider = Provider(provider_id="test-provider", mode="subprocess", command=["test"])

        assert isinstance(provider.id, ProviderId)
        assert str(provider.id) == "test-provider"

    def test_state_property(self):
        """Test state property is thread-safe."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # Should acquire lock and return state
        state = provider.state
        assert state == ProviderState.COLD

    def test_is_alive_property(self):
        """Test is_alive property."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        assert provider.is_alive is False

    def test_idle_time_property(self):
        """Test idle_time property."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # No last_used set
        assert provider.idle_time == 0.0

    def test_is_idle_property(self):
        """Test is_idle property."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # Not ready, so not idle
        assert provider.is_idle is False

    def test_meta_property(self):
        """Test meta property returns copy."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        meta = provider.meta
        assert isinstance(meta, dict)

    def test_lock_property(self):
        """Test lock property for backward compatibility."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # RLock is a factory function, check for lock-like interface
        lock = provider.lock
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")
        assert callable(lock.acquire)
        assert callable(lock.release)


class TestProviderShutdown:
    """Test Provider shutdown functionality."""

    def test_shutdown_cold_provider(self):
        """Test shutdown of cold provider."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        provider.shutdown()

        assert provider.state == ProviderState.COLD
        events = provider.collect_events()

        # Should have ProviderStopped event
        stopped_events = [e for e in events if isinstance(e, ProviderStopped)]
        assert len(stopped_events) == 1
        assert stopped_events[0].reason == "shutdown"

    def test_shutdown_clears_tools(self):
        """Test that shutdown clears tool catalog."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        # Add a tool manually for testing
        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="test", description="Test", input_schema={}))

        provider.shutdown()

        assert provider.tools.count() == 0


class TestProviderStatusDict:
    """Test Provider to_status_dict method."""

    def test_to_status_dict(self):
        """Test status dictionary generation."""
        provider = Provider(
            provider_id="test-provider",
            mode="subprocess",
            command=["python", "-m", "test"],
        )

        status = provider.to_status_dict()

        assert status["provider"] == "test-provider"
        assert status["state"] == "cold"
        assert status["alive"] is False
        assert status["mode"] == "subprocess"
        assert "health" in status
        assert "meta" in status

    def test_to_status_dict_includes_tools(self):
        """Test status dict includes cached tools."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="add", description="Add", input_schema={}))

        status = provider.to_status_dict()

        assert "add" in status["tools_cached"]


class TestProviderCompatibility:
    """Test backward compatibility methods."""

    def test_get_tool_names(self):
        """Test get_tool_names method."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="add", description="Add", input_schema={}))
        provider._tools.add(ToolSchema(name="sub", description="Sub", input_schema={}))

        names = provider.get_tool_names()

        assert set(names) == {"add", "sub"}

    def test_get_tools_dict(self):
        """Test get_tools_dict method."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        schema = ToolSchema(name="add", description="Add", input_schema={})
        provider._tools.add(schema)

        tools = provider.get_tools_dict()

        assert isinstance(tools, dict)
        assert "add" in tools
        assert tools["add"] == schema


class TestProviderThreadSafety:
    """Test Provider thread safety."""

    def test_concurrent_property_access(self):
        """Test concurrent access to properties."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        results = []
        errors = []

        def access_properties():
            try:
                for _ in range(100):
                    _ = provider.state
                    _ = provider.is_alive
                    _ = provider.last_used
                results.append(True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=access_properties) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10

    def test_concurrent_shutdown(self):
        """Test concurrent shutdown calls."""
        provider = Provider(provider_id="test", mode="subprocess", command=["test"])

        errors = []

        def shutdown():
            try:
                provider.shutdown()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=shutdown) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert provider.state == ProviderState.COLD


class TestProviderPredefinedTools:
    """Test Provider with pre-defined tools (lazy loading support)."""

    def test_create_provider_with_predefined_tools(self):
        """Test creating a provider with pre-defined tools."""
        tools = [
            {
                "name": "add",
                "description": "Add numbers",
                "inputSchema": {"type": "object"},
            },
            {
                "name": "multiply",
                "description": "Multiply numbers",
                "inputSchema": {"type": "object"},
            },
        ]
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
            tools=tools,
        )

        assert provider.state == ProviderState.COLD
        assert provider.has_tools is True
        assert provider.tools_predefined is True
        assert provider.tools.count() == 2
        assert "add" in provider.tools.list_names()
        assert "multiply" in provider.tools.list_names()

    def test_create_provider_without_predefined_tools(self):
        """Test creating a provider without pre-defined tools."""
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
        )

        assert provider.state == ProviderState.COLD
        assert provider.has_tools is False
        assert provider.tools_predefined is False
        assert provider.tools.count() == 0

    def test_predefined_tools_have_correct_schema(self):
        """Test that pre-defined tools maintain their schema."""
        tools = [
            {
                "name": "calculate",
                "description": "Perform calculation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            },
        ]
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
            tools=tools,
        )

        tool = provider.tools.get("calculate")
        assert tool is not None
        assert tool.name == "calculate"
        assert tool.description == "Perform calculation"
        assert tool.input_schema["type"] == "object"
        assert "a" in tool.input_schema["properties"]
        assert "b" in tool.input_schema["properties"]

    def test_predefined_tools_with_empty_list(self):
        """Test provider with empty tools list."""
        provider = Provider(
            provider_id="test",
            mode="subprocess",
            command=["test"],
            tools=[],
        )

        assert provider.has_tools is False
        assert provider.tools_predefined is False  # Empty list = no predefined tools
        assert provider.tools.count() == 0
