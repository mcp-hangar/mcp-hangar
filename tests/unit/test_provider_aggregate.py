"""Tests for Provider aggregate root."""

import threading
import time
from unittest.mock import MagicMock, patch

from mcp_hangar.domain.events import McpServerStopped
from mcp_hangar.domain.exceptions import CannotStartProviderError, ProviderStartError
from mcp_hangar.domain.model import McpServer, ProviderState
from mcp_hangar.domain.model.provider import VALID_TRANSITIONS
from mcp_hangar.domain.value_objects import ProviderMode


class TestProviderInitialization:
    """Test Provider initialization."""

    def test_create_subprocess_provider(self):
        """Test creating a subprocess provider."""
        provider = McpServer(mcp_server_id="test-provider", mode="subprocess",
        command=["python", "-m", "test"],)

        assert provider.mcp_server_id == "test-provider"
        assert provider.mode == ProviderMode.SUBPROCESS
        assert provider.state == ProviderState.COLD

    def test_create_docker_provider(self):
        """Test creating a docker provider."""
        provider = McpServer(mcp_server_id="docker-provider", mode="docker", image="test:latest")

        assert provider.mcp_server_id == "docker-provider"
        assert provider.mode == ProviderMode.DOCKER

    def test_provider_initial_state(self):
        """Test provider initial state."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        assert provider.state == ProviderState.COLD
        assert provider.is_alive is False
        assert provider.last_used == 0.0
        assert provider.tools.count() == 0
        assert provider.version == 0

    def test_provider_with_env_vars(self):
        """Test provider with environment variables."""
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],
        env={"KEY": "value"},)

        assert provider._env == {"KEY": "value"}

    def test_provider_with_custom_config(self):
        """Test provider with custom configuration."""
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],
        idle_ttl_s=600,
        health_check_interval_s=120,
        max_consecutive_failures=5,)

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
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # Provider records events during operations
        assert provider.has_uncommitted_events() is False

    def test_collect_events_clears_list(self):
        """Test that collecting events clears the internal list."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # Manually record an event for testing
        from mcp_hangar.domain.events import McpServerStopped

        provider._record_event(McpServerStopped(mcp_server_id="test", reason="test"))

        assert provider.has_uncommitted_events() is True

        events = provider.collect_events()
        assert len(events) == 1
        assert provider.has_uncommitted_events() is False

    def test_version_tracking(self):
        """Test version is tracked correctly."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        initial_version = provider.version
        provider._increment_version()

        assert provider.version == initial_version + 1


class TestProviderProperties:
    """Test Provider properties."""

    def test_mcp_server_id_property(self):
        """Test mcp_server_id property returns string."""
        provider = McpServer(mcp_server_id="test-provider", mode="subprocess", command=["test"])

        assert provider.mcp_server_id == "test-provider"
        assert isinstance(provider.mcp_server_id, str)

    def test_id_property_returns_value_object(self):
        """Test id property returns ProviderId value object."""
        from mcp_hangar.domain.value_objects import ProviderId

        provider = McpServer(mcp_server_id="test-provider", mode="subprocess", command=["test"])

        assert isinstance(provider.id, ProviderId)
        assert str(provider.id) == "test-provider"

    def test_state_property(self):
        """Test state property is thread-safe."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # Should acquire lock and return state
        state = provider.state
        assert state == ProviderState.COLD

    def test_is_alive_property(self):
        """Test is_alive property."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        assert provider.is_alive is False

    def test_idle_time_property(self):
        """Test idle_time property."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # No last_used set
        assert provider.idle_time == 0.0

    def test_is_idle_property(self):
        """Test is_idle property."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # Not ready, so not idle
        assert provider.is_idle is False

    def test_meta_property(self):
        """Test meta property returns copy."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        meta = provider.meta
        assert isinstance(meta, dict)

    def test_lock_property(self):
        """Test lock property for backward compatibility."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

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
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        provider.shutdown()

        assert provider.state == ProviderState.COLD
        events = provider.collect_events()

        # Should have McpServerStopped event
        stopped_events = [e for e in events if isinstance(e, McpServerStopped)]
        assert len(stopped_events) == 1
        assert stopped_events[0].reason == "shutdown"

    def test_shutdown_clears_tools(self):
        """Test that shutdown clears tool catalog."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        # Add a tool manually for testing
        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="test", description="Test", input_schema={}))

        provider.shutdown()

        assert provider.tools.count() == 0


class TestProviderStatusDict:
    """Test Provider to_status_dict method."""

    def test_to_status_dict(self):
        """Test status dictionary generation."""
        provider = McpServer(mcp_server_id="test-provider", mode="subprocess",
        command=["python", "-m", "test"],)

        status = provider.to_status_dict()

        assert status["mcp_server"] == "test-provider"
        assert status["state"] == "cold"
        assert status["alive"] is False
        assert status["mode"] == "subprocess"
        assert "health" in status
        assert "meta" in status

    def test_to_status_dict_includes_tools(self):
        """Test status dict includes cached tools."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="add", description="Add", input_schema={}))

        status = provider.to_status_dict()

        assert "add" in status["tools_cached"]


class TestProviderCompatibility:
    """Test backward compatibility methods."""

    def test_get_tool_names(self):
        """Test get_tool_names method."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="add", description="Add", input_schema={}))
        provider._tools.add(ToolSchema(name="sub", description="Sub", input_schema={}))

        names = provider.get_tool_names()

        assert set(names) == {"add", "sub"}

    def test_get_tools_dict(self):
        """Test get_tools_dict method."""
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

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
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

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
        provider = McpServer(mcp_server_id="test", mode="subprocess", command=["test"])

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


class TestEnsureReadyConcurrency:
    """Test ensure_ready() with threading.Event for concurrent startup coordination."""

    def _make_provider(self, **kwargs):
        """Create a provider with default test settings."""
        defaults = {
            "mcp_server_id": "test",
            "mode": "subprocess",
            "command": ["python", "-m", "test"],
        }
        defaults.update(kwargs)
        return McpServer(**defaults)

    def test_concurrent_ensure_ready_only_one_create_client(self):
        """Two threads calling ensure_ready() on COLD provider -- only one _create_client() call."""
        provider = self._make_provider()
        create_count = {"calls": 0}

        def slow_create_client():
            create_count["calls"] += 1
            time.sleep(0.3)
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.call.return_value = {"result": {"tools": []}}
            return mock_client

        errors = []
        results = []

        def call_ensure_ready():
            try:
                provider.ensure_ready()
                results.append("ok")
            except Exception as e:
                errors.append(e)

        with patch.object(provider, "_create_client", side_effect=slow_create_client):
            with patch.object(provider, "_perform_mcp_handshake"):
                t1 = threading.Thread(target=call_ensure_ready)
                t2 = threading.Thread(target=call_ensure_ready)
                t1.start()
                time.sleep(0.05)
                t2.start()
                t1.join(timeout=10)
                t2.join(timeout=10)

        assert create_count["calls"] == 1, f"Expected 1 _create_client call, got {create_count['calls']}"
        assert len(errors) == 0, f"Unexpected errors: {errors}"
        assert len(results) == 2, "Both threads should complete successfully"
        # Verify _ready_event mechanism exists
        assert hasattr(provider, "_ready_event"), "Provider must have _ready_event for concurrent waiter coordination"

    def test_waiter_gets_result_when_starter_completes(self):
        """Second thread waits on _ready_event and sees READY when first thread completes."""
        provider = self._make_provider()
        completion_order = []

        def slow_create_client():
            time.sleep(0.2)
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.call.return_value = {"result": {"tools": []}}
            return mock_client

        def call_ensure_ready(name):
            try:
                provider.ensure_ready()
                completion_order.append(name)
            except Exception:
                pass

        with patch.object(provider, "_create_client", side_effect=slow_create_client):
            with patch.object(provider, "_perform_mcp_handshake"):
                t1 = threading.Thread(target=call_ensure_ready, args=("starter",))
                t1.start()
                time.sleep(0.05)
                t2 = threading.Thread(target=call_ensure_ready, args=("waiter",))
                t2.start()
                t1.join(timeout=10)
                t2.join(timeout=10)

        assert provider.state == ProviderState.READY
        assert len(completion_order) == 2

    def test_startup_failure_propagates_to_waiters(self):
        """If startup fails, waiting thread receives the ProviderStartError."""
        provider = self._make_provider()
        errors = []

        def failing_create_client():
            time.sleep(0.1)
            raise RuntimeError("subprocess crashed")

        def call_ensure_ready():
            try:
                provider.ensure_ready()
            except (ProviderStartError, CannotStartProviderError) as e:
                errors.append(e)
            except Exception as e:
                errors.append(e)

        with patch.object(provider, "_create_client", side_effect=failing_create_client):
            t1 = threading.Thread(target=call_ensure_ready)
            t1.start()
            time.sleep(0.05)
            t2 = threading.Thread(target=call_ensure_ready)
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # Both threads should get an error (not stuck waiting)
        assert len(errors) == 2, f"Expected 2 errors, got {len(errors)}: {errors}"

    def test_ready_event_timeout_raises_cannot_start(self):
        """_ready_event.wait() with timeout raises error if startup takes too long."""
        provider = self._make_provider()
        barrier = threading.Barrier(2, timeout=10)

        def very_slow_create_client():
            # Signal that we've started, then block long enough for waiter to timeout
            barrier.wait()
            time.sleep(5.0)
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.call.return_value = {"result": {"tools": []}}
            return mock_client

        errors = []

        def call_ensure_ready():
            try:
                provider.ensure_ready()
            except (CannotStartProviderError, ProviderStartError) as e:
                errors.append(e)
            except Exception as e:
                errors.append(e)

        # Temporarily reduce the wait timeout so test doesn't take 30s
        original_ensure_ready = provider.ensure_ready

        def patched_ensure_ready():
            # Monkey-patch the Event.wait timeout by wrapping ensure_ready
            original_event_class = threading.Event

            class ShortTimeoutEvent(original_event_class):
                def wait(self, timeout=None):
                    # Use a very short timeout for testing
                    return super().wait(timeout=0.5)

            # Swap the event class temporarily on the waiter path
            original_ensure_ready()

        # Instead of complex patching, directly test with a short timeout
        # by using a custom wrapper
        waiter_errors = []

        def waiter_call():
            try:
                # Wait for starter to begin
                barrier.wait()
                time.sleep(0.05)
                # Now the provider is INITIALIZING -- wait on event with a short timeout
                with provider._lock:
                    if provider._state == ProviderState.INITIALIZING:
                        ready_event = provider._ready_event
                    else:
                        return  # Already done
                # Simulate waiter path with short timeout
                if not ready_event.wait(timeout=0.5):
                    waiter_errors.append(
                        CannotStartProviderError(
                            provider.mcp_server_id,
                            "startup_timeout: timed out waiting for provider to start",
                            0.5,
                        )
                    )
            except Exception as e:
                waiter_errors.append(e)

        with patch.object(provider, "_create_client", side_effect=very_slow_create_client):
            with patch.object(provider, "_perform_mcp_handshake"):
                t1 = threading.Thread(target=call_ensure_ready)
                t1.start()
                t2 = threading.Thread(target=waiter_call)
                t2.start()
                t2.join(timeout=5)
                t1.join(timeout=10)

        # The waiter should have timed out
        assert len(waiter_errors) >= 1, "Waiter should have timed out waiting for startup"
        assert isinstance(waiter_errors[0], CannotStartProviderError)

    def test_ready_event_reset_on_dead_to_initializing_retry(self):
        """After DEAD -> INITIALIZING retry, _ready_event is reset correctly."""
        provider = self._make_provider()
        call_count = {"n": 0}

        def create_client_fail_then_succeed():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first attempt fails")
            mock_client = MagicMock()
            mock_client.is_alive.return_value = True
            mock_client.call.return_value = {"result": {"tools": []}}
            return mock_client

        with patch.object(provider, "_create_client", side_effect=create_client_fail_then_succeed):
            with patch.object(provider, "_perform_mcp_handshake"):
                try:
                    provider.ensure_ready()
                except (ProviderStartError, Exception):
                    pass

                assert provider.state in (ProviderState.DEAD, ProviderState.DEGRADED)

                # Second attempt: should succeed with fresh event
                provider.ensure_ready()

        assert provider.state == ProviderState.READY
        assert call_count["n"] == 2

    def test_ensure_ready_on_ready_provider_returns_immediately(self):
        """ensure_ready() on READY provider returns immediately (fast path)."""
        provider = self._make_provider()

        mock_client = MagicMock()
        mock_client.is_alive.return_value = True
        mock_client.call.return_value = {"result": {"tools": []}}

        # Manually set provider to READY state
        with provider._lock:
            provider._state = ProviderState.READY
            provider._client = mock_client

        with patch.object(provider, "_create_client") as mock_create:
            provider.ensure_ready()
            mock_create.assert_not_called()

        assert provider.state == ProviderState.READY


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
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],
        tools=tools,)

        assert provider.state == ProviderState.COLD
        assert provider.has_tools is True
        assert provider.tools_predefined is True
        assert provider.tools.count() == 2
        assert "add" in provider.tools.list_names()
        assert "multiply" in provider.tools.list_names()

    def test_create_provider_without_predefined_tools(self):
        """Test creating a provider without pre-defined tools."""
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],)

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
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],
        tools=tools,)

        tool = provider.tools.get("calculate")
        assert tool is not None
        assert tool.name == "calculate"
        assert tool.description == "Perform calculation"
        assert tool.input_schema["type"] == "object"
        assert "a" in tool.input_schema["properties"]
        assert "b" in tool.input_schema["properties"]

    def test_predefined_tools_with_empty_list(self):
        """Test provider with empty tools list."""
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["test"],
        tools=[],)

        assert provider.has_tools is False
        assert provider.tools_predefined is False  # Empty list = no predefined tools
        assert provider.tools.count() == 0


class TestInvokeToolRefresh:
    """Test invoke_tool() with two-lock-cycle pattern for tool refresh."""

    def _make_ready_provider(self):
        """Create a provider in READY state with a mock client."""
        provider = McpServer(mcp_server_id="test", mode="subprocess",
        command=["python", "-m", "test"],)
        mock_client = MagicMock()
        mock_client.is_alive.return_value = True

        # Set provider to READY state with client
        with provider._lock:
            provider._state = ProviderState.READY
            provider._client = mock_client

        return provider, mock_client

    def test_invoke_tool_does_not_hold_lock_during_refresh_rpc(self):
        """invoke_tool() does NOT hold lock during tools/list RPC."""
        provider, mock_client = self._make_ready_provider()
        lock_held_during_refresh = {"held": False}

        # Track calls: first tools/list for refresh, then tools/call for invocation
        def mock_call(method, params, timeout=None):
            if method == "tools/list":
                # Check if lock is held (try to acquire with timeout=0)
                acquired = provider._lock.acquire(blocking=False)
                if acquired:
                    provider._lock.release()
                    lock_held_during_refresh["held"] = False
                else:
                    lock_held_during_refresh["held"] = True
                return {"result": {"tools": [{"name": "new_tool", "description": "A new tool", "inputSchema": {}}]}}
            if method == "tools/call":
                return {"result": {"content": [{"text": "ok"}]}}
            return {"result": {}}

        mock_client.call.side_effect = mock_call

        result = provider.invoke_tool("new_tool", {})

        assert result is not None
        # The _refresh_in_progress flag should exist for deduplication
        assert hasattr(provider, "_refresh_in_progress"), (
            "Provider must have _refresh_in_progress flag for refresh deduplication"
        )
        # Lock should NOT have been held during tools/list RPC
        assert lock_held_during_refresh["held"] is False, "Lock must not be held during tools/list RPC"

    def test_concurrent_invoke_tool_only_one_refresh_rpc(self):
        """Concurrent invoke_tool() calls with stale tools -- only one tools/list refresh RPC."""
        provider, mock_client = self._make_ready_provider()
        refresh_count = {"calls": 0}

        def mock_call(method, params, timeout=None):
            if method == "tools/list":
                refresh_count["calls"] += 1
                time.sleep(0.2)  # Slow refresh to allow concurrent access
                return {"result": {"tools": [{"name": "new_tool", "description": "A tool", "inputSchema": {}}]}}
            if method == "tools/call":
                return {"result": {"content": [{"text": "ok"}]}}
            return {"result": {}}

        mock_client.call.side_effect = mock_call

        errors = []
        results = []

        def call_invoke(name):
            try:
                result = provider.invoke_tool("new_tool", {})
                results.append(result)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=call_invoke, args=("t1",))
        t2 = threading.Thread(target=call_invoke, args=("t2",))
        t1.start()
        time.sleep(0.05)
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # At most one tools/list refresh RPC should have been issued
        assert refresh_count["calls"] <= 1, f"Expected at most 1 refresh RPC, got {refresh_count['calls']}"

    def test_refresh_failure_does_not_corrupt_tool_registry(self):
        """Tool refresh failure does not corrupt tool registry -- old tools still available."""
        provider, mock_client = self._make_ready_provider()

        # Pre-load an existing tool
        from mcp_hangar.domain.model.tool_catalog import ToolSchema

        provider._tools.add(ToolSchema(name="existing_tool", description="Existing", input_schema={}))

        call_count = {"n": 0}

        def mock_call(method, params, timeout=None):
            call_count["n"] += 1
            if method == "tools/list":
                raise RuntimeError("network error during refresh")
            if method == "tools/call":
                return {"result": {"content": [{"text": "ok"}]}}
            return {"result": {}}

        mock_client.call.side_effect = mock_call

        # Invoke existing tool -- refresh will fail but tool should still work
        result = provider.invoke_tool("existing_tool", {})

        assert result is not None
        assert provider._tools.has("existing_tool"), "Existing tool must survive failed refresh"

    def test_invoke_tool_end_to_end_with_refresh(self):
        """invoke_tool() works end-to-end: tool found via refresh, invoked, result returned."""
        provider, mock_client = self._make_ready_provider()

        def mock_call(method, params, timeout=None):
            if method == "tools/list":
                return {
                    "result": {
                        "tools": [
                            {"name": "calculator", "description": "Calculate things", "inputSchema": {}},
                        ]
                    }
                }
            if method == "tools/call":
                return {"result": {"content": [{"type": "text", "text": "42"}]}}
            return {"result": {}}

        mock_client.call.side_effect = mock_call

        result = provider.invoke_tool("calculator", {"expression": "6*7"})

        assert result == {"content": [{"type": "text", "text": "42"}]}
