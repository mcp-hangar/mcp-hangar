"""Provider aggregate root - the main domain entity."""

import threading
import time
from typing import Any, TYPE_CHECKING

from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...infrastructure.lock_hierarchy import TrackedLock
from ..contracts.metrics_publisher import IMetricsPublisher, NullMetricsPublisher
from ..events import (
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderIdleDetected,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from ..exceptions import (
    CannotStartProviderError,
    InvalidStateTransitionError,
    ProviderStartError,
    ToolInvocationError,
    ToolNotFoundError,
)
from ..services.error_diagnostics import collect_startup_diagnostics
from ..value_objects import CorrelationId, HealthCheckInterval, IdleTTL, ProviderId, ProviderMode, ProviderState
from .aggregate import AggregateRoot
from .health_tracker import HealthTracker
from .tool_catalog import ToolCatalog, ToolSchema

logger = get_logger(__name__)


# Valid state transitions
VALID_TRANSITIONS = {
    ProviderState.COLD: {ProviderState.INITIALIZING},
    ProviderState.INITIALIZING: {
        ProviderState.READY,
        ProviderState.DEAD,
        ProviderState.DEGRADED,
    },
    ProviderState.READY: {
        ProviderState.COLD,
        ProviderState.DEAD,
        ProviderState.DEGRADED,
    },
    ProviderState.DEGRADED: {ProviderState.INITIALIZING, ProviderState.COLD},
    ProviderState.DEAD: {ProviderState.INITIALIZING, ProviderState.DEGRADED},
}


class Provider(AggregateRoot):
    """
    Provider aggregate root.

    Manages the complete lifecycle of an MCP provider including:
    - State machine with valid transitions
    - Health tracking and circuit breaker logic
    - Tool catalog management
    - Process/client management

    All public operations are thread-safe using internal locking.
    """

    def __init__(
        self,
        provider_id: str,
        mode: str | ProviderMode,  # Accept both string and enum
        command: list[str] | None = None,
        image: str | None = None,
        endpoint: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int | IdleTTL = 300,  # Accept both int and value object
        health_check_interval_s: int | HealthCheckInterval = 60,  # Accept both int and value object
        max_consecutive_failures: int = 3,
        # Container-specific options
        volumes: list[str] | None = None,
        build: dict[str, str] | None = None,
        resources: dict[str, str] | None = None,
        network: str = "none",
        read_only: bool = True,
        user: str | None = None,  # UID:GID or username
        description: str | None = None,  # Description/preprompt for AI models
        # Pre-defined tools (allows visibility before provider starts)
        tools: list[dict[str, Any]] | None = None,
        # HTTP transport options (for remote mode)
        auth: dict[str, Any] | None = None,  # Authentication config
        tls: dict[str, Any] | None = None,  # TLS config
        http: dict[str, Any] | None = None,  # HTTP transport config
        # Dependencies
        metrics_publisher: IMetricsPublisher | None = None,
    ):
        super().__init__()

        # Identity
        self._id = ProviderId(provider_id)

        # Mode - normalize to ProviderMode enum (container -> docker)
        self._mode = ProviderMode.normalize(mode)

        self._description = description

        # Configuration - normalize to value objects
        self._command = command
        self._image = image
        self._endpoint = endpoint
        self._env = env or {}

        # Idle TTL - normalize to value object
        if isinstance(idle_ttl_s, IdleTTL):
            self._idle_ttl = idle_ttl_s
        else:
            self._idle_ttl = IdleTTL(idle_ttl_s)

        # Health check interval - normalize to value object
        if isinstance(health_check_interval_s, HealthCheckInterval):
            self._health_check_interval = health_check_interval_s
        else:
            self._health_check_interval = HealthCheckInterval(health_check_interval_s)

        # Container-specific configuration
        self._volumes = volumes or []
        self._build = build  # {"dockerfile": "...", "context": "..."}
        self._resources = resources or {"memory": "512m", "cpu": "1.0"}
        self._network = network
        self._read_only = read_only
        self._user = user

        # HTTP transport configuration (for remote mode)
        self._auth_config = auth
        self._tls_config = tls
        self._http_config = http

        # Dependencies (Dependency Inversion Principle)
        self._metrics_publisher = metrics_publisher or NullMetricsPublisher()

        # State
        self._state = ProviderState.COLD
        self._health = HealthTracker(max_consecutive_failures=max_consecutive_failures)
        self._tools = ToolCatalog()
        self._client: Any | None = None  # StdioClient or HttpClient
        self._meta: dict[str, Any] = {}
        self._last_used: float = 0.0

        # Pre-load tools from configuration (allows visibility before start)
        self._tools_predefined = False
        if tools:
            self._tools.update_from_list(tools)
            self._tools_predefined = True

        # Thread safety
        # Lock hierarchy level: PROVIDER (10)
        # Safe to acquire after: (none - this is top level for domain)
        # Safe to acquire before: EVENT_BUS, EVENT_STORE, STDIO_CLIENT
        # I/O rule: Copy client reference under lock, do I/O outside lock
        self._lock = self._create_lock(provider_id)

    @staticmethod
    def _create_lock(provider_id: str) -> "TrackedLock | threading.RLock":
        """Create lock with hierarchy tracking.

        Uses runtime import to avoid circular dependency between
        domain and infrastructure layers.
        """
        try:
            from ...infrastructure.lock_hierarchy import LockLevel, TrackedLock

            return TrackedLock(LockLevel.PROVIDER, f"Provider:{provider_id}")
        except ImportError:
            # Fallback for testing or isolated domain usage
            return threading.RLock()

    # --- Properties ---

    @property
    def id(self) -> ProviderId:
        """Provider identifier."""
        return self._id

    @property
    def provider_id(self) -> str:
        """Provider identifier as string (for backward compatibility)."""
        return str(self._id)

    @property
    def mode(self) -> ProviderMode:
        """Provider mode enum."""
        return self._mode

    @property
    def mode_str(self) -> str:
        """Provider mode as string (for backward compatibility)."""
        return self._mode.value

    @property
    def description(self) -> str | None:
        """Provider description for AI models."""
        return self._description

    @property
    def state(self) -> ProviderState:
        """Current provider state."""
        with self._lock:
            return self._state

    @property
    def health(self) -> HealthTracker:
        """Health tracker."""
        return self._health

    @property
    def tools(self) -> ToolCatalog:
        """Tool catalog."""
        return self._tools

    @property
    def has_tools(self) -> bool:
        """Check if provider has any tools registered (predefined or discovered)."""
        return self._tools.count() > 0

    @property
    def tools_predefined(self) -> bool:
        """Check if tools were predefined in configuration (no startup needed for visibility)."""
        return self._tools_predefined

    @property
    def is_alive(self) -> bool:
        """Check if provider client is alive."""
        with self._lock:
            return self._client is not None and self._client.is_alive()

    @property
    def last_used(self) -> float:
        """Timestamp of last tool invocation."""
        with self._lock:
            return self._last_used

    @property
    def idle_time(self) -> float:
        """Time since last use in seconds."""
        with self._lock:
            if self._last_used == 0:
                return 0.0
            return time.time() - self._last_used

    @property
    def is_idle(self) -> bool:
        """Check if provider has been idle longer than TTL."""
        with self._lock:
            if self._state != ProviderState.READY:
                return False
            if self._last_used == 0:
                return False
            return self.idle_time > self._idle_ttl.seconds

    @property
    def meta(self) -> dict[str, Any]:
        """Provider metadata."""
        with self._lock:
            return dict(self._meta)

    @property
    def lock(self) -> "TrackedLock | threading.RLock":
        """Get the internal lock (for backward compatibility)."""
        return self._lock

    # --- State Management ---

    def _transition_to(self, new_state: ProviderState) -> None:
        """
        Transition to a new state (must hold lock).

        Validates the transition is valid according to state machine rules.
        Records a ProviderStateChanged event.
        """
        if new_state == self._state:
            return

        if new_state not in VALID_TRANSITIONS.get(self._state, set()):
            raise InvalidStateTransitionError(self.provider_id, str(self._state.value), str(new_state.value))

        old_state = self._state
        self._state = new_state
        self._increment_version()

        self._record_event(
            ProviderStateChanged(
                provider_id=self.provider_id,
                old_state=str(old_state.value),
                new_state=str(new_state.value),
            )
        )

    def _can_start(self) -> tuple:
        """
        Check if provider can be started (must hold lock).

        Returns: (can_start, reason, time_until_retry)
        """
        if self._state == ProviderState.READY:
            if self._client and self._client.is_alive():
                return True, "already_ready", 0

        if self._state == ProviderState.DEGRADED:
            if not self._health.can_retry():
                time_left = self._health.time_until_retry()
                return False, "backoff_not_elapsed", time_left

        return True, "", 0

    # --- Business Operations ---

    def ensure_ready(self) -> None:
        """
        Ensure provider is in READY state, starting if necessary.

        Thread-safe. Blocks until ready or raises exception.

        Raises:
            CannotStartProviderError: If backoff hasn't elapsed
            ProviderStartError: If provider fails to start
        """
        with self._lock:
            # Fast path - already ready
            if self._state == ProviderState.READY:
                if self._client and self._client.is_alive():
                    return
                # Client died
                logger.warning(f"provider_dead: {self.provider_id}")
                self._state = ProviderState.DEAD

            # Check if we can start
            can_start, reason, time_left = self._can_start()
            if not can_start:
                raise CannotStartProviderError(
                    self.provider_id,
                    f"backoff not elapsed, retry in {time_left:.1f}s",
                    time_left,
                )

            # Start if needed
            if self._state in (
                ProviderState.COLD,
                ProviderState.DEAD,
                ProviderState.DEGRADED,
            ):
                self._start()

    def _start(self) -> None:
        """
        Start provider process (must hold lock).

        Handles subprocess, docker, container modes.
        """
        start_time = time.time()
        self._transition_to(ProviderState.INITIALIZING)

        cold_start_time = self._begin_cold_start_tracking()
        client = None  # Track client for diagnostics on failure

        try:
            client = self._create_client()
            self._perform_mcp_handshake(client)
            self._finalize_start(client, start_time)
            self._end_cold_start_tracking(cold_start_time, success=True)

        except ProviderStartError as e:
            self._end_cold_start_tracking(cold_start_time, success=False)
            self._handle_start_failure(e)
            raise
        except Exception as e:
            self._end_cold_start_tracking(cold_start_time, success=False)
            self._handle_start_failure(e)

            # Collect diagnostics from client if available
            diagnostics = self._collect_startup_diagnostics(client) if client else {}

            raise ProviderStartError(
                provider_id=self.provider_id,
                reason=str(e),
                stderr=diagnostics.get("stderr"),
                exit_code=diagnostics.get("exit_code"),
                suggestion=diagnostics.get("suggestion"),
            ) from e

    def _begin_cold_start_tracking(self) -> float | None:
        """Begin tracking cold start metrics. Returns start timestamp."""
        try:
            self._metrics_publisher.begin_cold_start(self.provider_id)
            return time.time()
        except Exception:
            return None

    def _end_cold_start_tracking(self, start_time: float | None, success: bool) -> None:
        """End cold start tracking and record metrics."""
        if start_time is None:
            return
        try:
            if success:
                duration = time.time() - start_time
                self._metrics_publisher.record_cold_start(self.provider_id, duration, self._mode.value)
            self._metrics_publisher.end_cold_start(self.provider_id)
        except Exception:
            pass

    def _create_client(self) -> Any:
        """Create and return the appropriate client based on mode."""
        from ..services.provider_launcher import get_launcher

        launcher = get_launcher(self._mode.value)
        config = self._get_launch_config()
        return launcher.launch(**config)

    def _get_launch_config(self) -> dict[str, Any]:
        """Get launch configuration for the current mode."""
        if self._mode == ProviderMode.SUBPROCESS:
            return {"command": self._command, "env": self._env}

        if self._mode == ProviderMode.DOCKER:
            return {
                "image": self._image,
                "volumes": self._volumes,
                "env": self._env,
                "memory_limit": self._resources.get("memory", "512m"),
                "cpu_limit": self._resources.get("cpu", "1.0"),
                "network": self._network,
                "read_only": self._read_only,
                "user": self._user,
            }

        if self._mode.value in ("container", "podman"):
            return {
                "image": self._get_container_image(),
                "volumes": self._volumes,
                "env": self._env,
                "memory_limit": self._resources.get("memory", "512m"),
                "cpu_limit": self._resources.get("cpu", "1.0"),
                "network": self._network,
                "read_only": self._read_only,
                "user": self._user,
            }

        if self._mode == ProviderMode.REMOTE:
            return {
                "endpoint": self._endpoint,
                "auth_config": self._auth_config,
                "tls_config": self._tls_config,
                "http_config": self._http_config,
            }

        raise ValueError(f"unsupported_mode: {self._mode.value}")

    def _get_container_image(self) -> str:
        """Get or build container image."""
        from ..services.image_builder import BuildConfig, get_image_builder

        if self._build and self._build.get("dockerfile"):
            runtime = "podman" if self._mode.value == "podman" else "auto"
            builder = get_image_builder(runtime=runtime)
            build_config = BuildConfig(
                dockerfile=self._build["dockerfile"],
                context=self._build.get("context", "."),
                tag=self._build.get("tag"),
            )
            image = builder.build_if_needed(build_config)
            logger.info(f"Built image for {self.provider_id}: {image}")
            return image

        if not self._image:
            raise ProviderStartError(
                self.provider_id,
                "Container mode requires 'image' or 'build.dockerfile'",
            )
        return self._image

    def _perform_mcp_handshake(self, client: Any) -> None:
        """Perform MCP initialize and tools/list handshake."""
        # Initialize
        # Note: timeout is handled by the client's configuration
        # (StdioClient: 15s default, HttpClient: configured read_timeout)
        init_resp = client.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-registry", "version": "1.0.0"},
            },
        )

        if "error" in init_resp:
            error_msg = init_resp["error"].get("message", "unknown")
            self._log_client_error(client, error_msg)

            # Collect full diagnostics for user-friendly error
            diagnostics = self._collect_startup_diagnostics(client)
            raise ProviderStartError(
                provider_id=self.provider_id,
                reason=f"MCP initialization failed: {error_msg}",
                stderr=diagnostics.get("stderr"),
                exit_code=diagnostics.get("exit_code"),
                suggestion=diagnostics.get("suggestion")
                or "Check provider logs and ensure it implements the MCP protocol correctly.",
            )

        # Discover tools
        tools_resp = client.call("tools/list", {})
        if "error" in tools_resp:
            error_msg = tools_resp["error"].get("message", "unknown")
            diagnostics = self._collect_startup_diagnostics(client)
            raise ProviderStartError(
                provider_id=self.provider_id,
                reason=f"Failed to list tools: {error_msg}",
                stderr=diagnostics.get("stderr"),
                exit_code=diagnostics.get("exit_code"),
                suggestion=diagnostics.get("suggestion")
                or "Provider started but tools/list failed. Check provider implementation.",
            )

        tool_list = tools_resp.get("result", {}).get("tools", [])
        self._tools.update_from_list(tool_list)

    def _log_client_error(self, client: Any, error_msg: str) -> None:
        """Log detailed error info including stderr and exit code for debugging."""
        proc = getattr(client, "process", None)
        if not proc:
            return

        # Log exit code
        try:
            rc = proc.poll()
            if rc is not None:
                logger.error(f"provider_process_exit_code: {rc}")
        except Exception:
            pass

        # Try to capture stderr (may already be captured by StdioClient)
        last_stderr = getattr(client, "_last_stderr", None)
        if last_stderr:
            logger.error(f"provider_stderr: {last_stderr}")
            return

        # Fallback: try to read stderr directly
        stderr = getattr(proc, "stderr", None)
        if stderr:
            try:
                err_bytes = stderr.read()
                if err_bytes:
                    err_text = (err_bytes if isinstance(err_bytes, str) else err_bytes.decode(errors="replace")).strip()
                    if err_text:
                        logger.error(f"provider_stderr: {err_text}")
            except Exception:
                pass

    def _collect_startup_diagnostics(self, client: Any) -> dict[str, Any]:
        """Collect diagnostic information from a failed client/process.

        Delegates to domain service for actual collection logic.
        """
        return collect_startup_diagnostics(client)

    def _finalize_start(self, client: Any, start_time: float) -> None:
        """Finalize successful provider start."""
        self._client = client
        self._meta = {
            "init_result": {},
            "tools_count": self._tools.count(),
            "started_at": time.time(),
        }
        self._transition_to(ProviderState.READY)
        self._health.record_success()
        self._last_used = time.time()

        startup_duration_ms = (time.time() - start_time) * 1000
        self._record_event(
            ProviderStarted(
                provider_id=self.provider_id,
                mode=self._mode.value,
                tools_count=self._tools.count(),
                startup_duration_ms=startup_duration_ms,
            )
        )

        logger.info(f"provider_started: {self.provider_id}, mode={self._mode.value}, tools={self._tools.count()}")

    def _handle_start_failure(self, error: Exception | None) -> None:
        """Handle start failure (must hold lock)."""
        # Clean up client if partially started
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

        self._health.record_failure()

        error_str = str(error) if error else "unknown error"

        # Determine new state
        if self._health.should_degrade():
            # Use direct assignment to avoid transition validation issues
            self._state = ProviderState.DEGRADED
            self._increment_version()

            logger.warning(f"provider_degraded: {self.provider_id}, failures={self._health.consecutive_failures}")

            self._record_event(
                ProviderDegraded(
                    provider_id=self.provider_id,
                    consecutive_failures=self._health.consecutive_failures,
                    total_failures=self._health.total_failures,
                    reason=error_str,
                )
            )
        else:
            self._state = ProviderState.DEAD
            self._increment_version()

        logger.error(f"provider_start_failed: {self.provider_id}, error={error_str}")

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """
        Invoke a tool on this provider.

        Thread-safe. Ensures provider is ready before invocation.

        Note: This method follows the "copy reference under lock, I/O outside lock"
        pattern to avoid blocking other operations during tool invocation.

        Args:
            tool_name: Name of the tool to invoke
            arguments: Tool arguments
            timeout: Timeout in seconds

        Returns:
            Tool result dictionary

        Raises:
            CannotStartProviderError: If provider cannot be started
            ToolNotFoundError: If tool doesn't exist
            ToolInvocationError: If invocation fails
        """
        correlation_id = str(CorrelationId())

        # Phase 1: Validation and state update under lock
        with self._lock:
            # Ensure ready
            self.ensure_ready()

            # Check tool exists
            if not self._tools.has(tool_name):
                # Try refreshing tools once
                self._refresh_tools()

            if not self._tools.has(tool_name):
                raise ToolNotFoundError(self.provider_id, tool_name)

            self._health._total_invocations += 1

            # Copy client reference - safe because client is stable once READY
            # Any state transition that invalidates client must acquire this lock first
            client = self._client

            # Record start event
            self._record_event(
                ToolInvocationRequested(
                    provider_id=self.provider_id,
                    tool_name=tool_name,
                    correlation_id=correlation_id,
                    arguments=arguments,
                )
            )

        # Phase 2: I/O outside lock (allows concurrent reads on provider state)
        start_time = time.time()
        response = None
        invocation_error = None

        try:
            response = client.call(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=timeout,
            )
        except Exception as e:
            invocation_error = e

        # Phase 3: Update state based on result (under lock)
        with self._lock:
            if invocation_error is not None:
                self._health.record_failure()

                self._record_event(
                    ToolInvocationFailed(
                        provider_id=self.provider_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        error_message=str(invocation_error),
                        error_type=type(invocation_error).__name__,
                    )
                )

                logger.error(
                    f"tool_invocation_failed: {correlation_id}, "
                    f"provider={self.provider_id}, tool={tool_name}, error={invocation_error}"
                )

                raise ToolInvocationError(
                    self.provider_id,
                    str(invocation_error),
                    {"tool_name": tool_name, "correlation_id": correlation_id},
                ) from invocation_error

            if "error" in response:
                error_msg = response["error"].get("message", "unknown")
                self._health.record_invocation_failure()

                self._record_event(
                    ToolInvocationFailed(
                        provider_id=self.provider_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        error_message=error_msg,
                        error_type=str(response["error"].get("code", "unknown")),
                    )
                )

                raise ToolInvocationError(
                    self.provider_id,
                    f"tool_error: {error_msg}",
                    {"tool_name": tool_name, "correlation_id": correlation_id},
                )

            # Success
            duration_ms = (time.time() - start_time) * 1000
            self._health.record_success()
            self._last_used = time.time()

            result = response.get("result", {})
            self._record_event(
                ToolInvocationCompleted(
                    provider_id=self.provider_id,
                    tool_name=tool_name,
                    correlation_id=correlation_id,
                    duration_ms=duration_ms,
                    result_size_bytes=len(str(result)),
                )
            )

            logger.debug(f"tool_invoked: {correlation_id}, provider={self.provider_id}, tool={tool_name}")

            return result

    def _refresh_tools(self) -> None:
        """Refresh tool catalog from provider (must hold lock)."""
        if not self._client or not self._client.is_alive():
            return

        try:
            tools_resp = self._client.call("tools/list", {}, timeout=5.0)
            if "result" in tools_resp:
                tool_list = tools_resp.get("result", {}).get("tools", [])
                self._tools.update_from_list(tool_list)
        except Exception as e:
            logger.warning(f"tool_refresh_failed: {self.provider_id}, error={e}")

    def health_check(self) -> bool:
        """
        Perform active health check.

        Thread-safe. Returns True if healthy.

        Note: Follows "copy reference under lock, I/O outside lock" pattern.
        """
        # Phase 1: Check state and get client reference under lock
        with self._lock:
            if self._state != ProviderState.READY:
                return False

            if not self._client or not self._client.is_alive():
                self._state = ProviderState.DEAD
                self._increment_version()
                return False

            # Copy client reference for I/O outside lock
            client = self._client

        # Phase 2: Perform health check I/O outside lock
        start_time = time.time()
        check_error = None
        response = None

        try:
            response = client.call("tools/list", {}, timeout=5.0)
            if "error" in response:
                check_error = Exception(response["error"].get("message", "unknown"))
        except Exception as e:
            check_error = e

        # Phase 3: Update state based on result under lock
        with self._lock:
            # Re-check state in case it changed during I/O
            if self._state != ProviderState.READY:
                return False

            if check_error is not None:
                self._health.record_failure()

                self._record_event(
                    HealthCheckFailed(
                        provider_id=self.provider_id,
                        consecutive_failures=self._health.consecutive_failures,
                        error_message=str(check_error),
                    )
                )

                logger.warning(f"health_check_failed: {self.provider_id}, error={check_error}")

                if self._health.should_degrade():
                    self._state = ProviderState.DEGRADED
                    self._increment_version()

                    logger.warning(f"provider_degraded_by_health_check: {self.provider_id}")

                    self._record_event(
                        ProviderDegraded(
                            provider_id=self.provider_id,
                            consecutive_failures=self._health.consecutive_failures,
                            total_failures=self._health.total_failures,
                            reason="health_check_failures",
                        )
                    )

                return False

            # Success
            duration_ms = (time.time() - start_time) * 1000
            self._health.record_success()

            self._record_event(HealthCheckPassed(provider_id=self.provider_id, duration_ms=duration_ms))

            return True

    def maybe_shutdown_idle(self) -> bool:
        """
        Shutdown if idle past TTL.

        Thread-safe. Returns True if shutdown was performed.
        """
        with self._lock:
            if self._state != ProviderState.READY:
                return False

            idle_time = time.time() - self._last_used
            if idle_time > self._idle_ttl.seconds:
                self._record_event(
                    ProviderIdleDetected(
                        provider_id=self.provider_id,
                        idle_duration_s=idle_time,
                        last_used_at=self._last_used,
                    )
                )

                logger.info(f"provider_idle_shutdown: {self.provider_id}, idle={idle_time:.1f}s")
                self._shutdown_internal(reason="idle")
                return True

            return False

    def shutdown(self) -> None:
        """Explicit shutdown (public API). Thread-safe."""
        with self._lock:
            self._shutdown_internal(reason="shutdown")

    def stop(self) -> None:
        """Stop the provider. Alias for shutdown(). Thread-safe."""
        self.shutdown()

    def _shutdown_internal(self, reason: str = "shutdown") -> None:
        """Shutdown implementation (must hold lock)."""
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.warning(f"shutdown_error: {self.provider_id}, error={e}")
            self._client = None

        self._state = ProviderState.COLD
        self._increment_version()
        self._tools.clear()
        self._meta.clear()

        self._record_event(ProviderStopped(provider_id=self.provider_id, reason=reason))

    # --- Compatibility Methods ---

    def get_tool_names(self) -> list[str]:
        """Get list of available tool names."""
        with self._lock:
            return self._tools.list_names()

    def get_tools_dict(self) -> dict[str, ToolSchema]:
        """Get tools as dictionary (for backward compatibility)."""
        with self._lock:
            return self._tools.to_dict()

    def to_status_dict(self) -> dict[str, Any]:
        """Get status as dictionary (for registry.list)."""
        with self._lock:
            return {
                "provider": self.provider_id,
                "state": self._state.value,
                "alive": self._client is not None and self._client.is_alive(),
                "mode": self._mode.value,
                "image_or_command": self._image or self._command,
                "tools_cached": self._tools.list_names(),
                "health": self._health.to_dict(),
                "meta": dict(self._meta),
            }
