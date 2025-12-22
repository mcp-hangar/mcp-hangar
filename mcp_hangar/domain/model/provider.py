"""Provider aggregate root - the main domain entity."""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

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
from ..value_objects import CorrelationId, ProviderId, ProviderState
from .aggregate import AggregateRoot
from .health_tracker import HealthTracker
from .tool_catalog import ToolCatalog, ToolSchema


# Metrics imports (lazy to avoid circular imports)
def _get_metrics():
    from ...metrics import cold_start_begin, cold_start_end, record_cold_start

    return record_cold_start, cold_start_begin, cold_start_end


logger = logging.getLogger(__name__)


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
        mode: str,
        command: Optional[List[str]] = None,
        image: Optional[str] = None,
        endpoint: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        idle_ttl_s: int = 300,
        health_check_interval_s: int = 60,
        max_consecutive_failures: int = 3,
        # Container-specific options
        volumes: Optional[List[str]] = None,
        build: Optional[Dict[str, str]] = None,
        resources: Optional[Dict[str, str]] = None,
        network: str = "none",
        read_only: bool = True,
        user: Optional[str] = None,  # UID:GID or username
        description: Optional[str] = None,  # Description/preprompt for AI models
    ):
        super().__init__()

        # Identity
        self._id = ProviderId(provider_id)
        self._mode = mode
        self._description = description

        # Configuration
        self._command = command
        self._image = image
        self._endpoint = endpoint
        self._env = env or {}
        self._idle_ttl_s = idle_ttl_s
        self._health_check_interval_s = health_check_interval_s

        # Container-specific configuration
        self._volumes = volumes or []
        self._build = build  # {"dockerfile": "...", "context": "..."}
        self._resources = resources or {"memory": "512m", "cpu": "1.0"}
        self._network = network
        self._read_only = read_only
        self._user = user

        # State
        self._state = ProviderState.COLD
        self._health = HealthTracker(max_consecutive_failures=max_consecutive_failures)
        self._tools = ToolCatalog()
        self._client: Optional[Any] = None  # StdioClient
        self._meta: Dict[str, Any] = {}
        self._last_used: float = 0.0

        # Thread safety
        self._lock = threading.RLock()

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
    def mode(self) -> str:
        """Provider mode (subprocess, docker, remote)."""
        return self._mode

    @property
    def description(self) -> Optional[str]:
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
            return self.idle_time > self._idle_ttl_s

    @property
    def meta(self) -> Dict[str, Any]:
        """Provider metadata."""
        with self._lock:
            return dict(self._meta)

    @property
    def lock(self) -> threading.RLock:
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
            raise InvalidStateTransitionError(
                self.provider_id, str(self._state.value), str(new_state.value)
            )

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

        cold_start_tracker = self._begin_cold_start_tracking()

        try:
            client = self._create_client()
            self._perform_mcp_handshake(client)
            self._finalize_start(client, start_time)
            self._end_cold_start_tracking(cold_start_tracker, start_time)

        except ProviderStartError:
            self._end_cold_start_tracking(cold_start_tracker, None)
            self._handle_start_failure(None)
            raise
        except Exception as e:
            self._end_cold_start_tracking(cold_start_tracker, None)
            self._handle_start_failure(e)
            raise ProviderStartError(self.provider_id, str(e)) from e

    def _begin_cold_start_tracking(self) -> Optional[tuple]:
        """Begin tracking cold start metrics."""
        try:
            record_cold_start, cold_start_begin, cold_start_end = _get_metrics()
            cold_start_begin(self.provider_id)
            return (record_cold_start, cold_start_end)
        except Exception:
            return None

    def _end_cold_start_tracking(
        self, tracker: Optional[tuple], start_time: Optional[float]
    ) -> None:
        """End cold start tracking and record metrics."""
        if not tracker:
            return
        record_cold_start, cold_start_end = tracker
        try:
            if start_time:
                duration = time.time() - start_time
                record_cold_start(self.provider_id, duration, self._mode)
            cold_start_end(self.provider_id)
        except Exception:
            pass

    def _create_client(self) -> Any:
        """Create and return the appropriate client based on mode."""
        from ..services.provider_launcher import get_launcher

        launcher = get_launcher(self._mode)
        config = self._get_launch_config()
        return launcher.launch(**config)

    def _get_launch_config(self) -> Dict[str, Any]:
        """Get launch configuration for the current mode."""
        if self._mode == "subprocess":
            return {"command": self._command, "env": self._env}

        if self._mode == "docker":
            return {"image": self._image, "env": self._env}

        if self._mode in ("container", "podman"):
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

        raise ValueError(f"unsupported_mode: {self._mode}")

    def _get_container_image(self) -> str:
        """Get or build container image."""
        from ..services.image_builder import BuildConfig, get_image_builder

        if self._build and self._build.get("dockerfile"):
            runtime = "podman" if self._mode == "podman" else "auto"
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
        init_resp = client.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-registry", "version": "1.0.0"},
            },
            timeout=10.0,
        )

        if "error" in init_resp:
            error_msg = init_resp["error"].get("message", "unknown")
            self._log_client_error(client, error_msg)
            raise ProviderStartError(self.provider_id, f"init_failed: {error_msg}")

        # Discover tools
        tools_resp = client.call("tools/list", {}, timeout=10.0)
        if "error" in tools_resp:
            error_msg = tools_resp["error"].get("message", "unknown")
            raise ProviderStartError(self.provider_id, f"tools_list_failed: {error_msg}")

        tool_list = tools_resp.get("result", {}).get("tools", [])
        self._tools.update_from_list(tool_list)

    def _log_client_error(self, client: Any, error_msg: str) -> None:
        """Log detailed error info for debugging (especially in CI)."""
        if error_msg != "reader_died":
            return

        proc = getattr(client, "process", None)
        if not proc:
            return

        # Try to capture stderr
        stderr = getattr(proc, "stderr", None)
        if stderr:
            try:
                err_bytes = stderr.read()
                if err_bytes:
                    err_text = (
                        err_bytes
                        if isinstance(err_bytes, str)
                        else err_bytes.decode(errors="replace")
                    ).strip()
                    if err_text:
                        logger.error(f"provider_container_stderr: {err_text}")
            except Exception:
                pass

        # Log exit code
        try:
            rc = proc.poll()
            if rc is not None:
                logger.error(f"provider_process_exit_code: {rc}")
        except Exception:
            pass

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
                mode=self._mode,
                tools_count=self._tools.count(),
                startup_duration_ms=startup_duration_ms,
            )
        )

        logger.info(
            f"provider_started: {self.provider_id}, mode={self._mode}, "
            f"tools={self._tools.count()}"
        )

    def _handle_start_failure(self, error: Optional[Exception]) -> None:
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

            logger.warning(
                f"provider_degraded: {self.provider_id}, "
                f"failures={self._health.consecutive_failures}"
            )

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

    def invoke_tool(
        self, tool_name: str, arguments: Dict[str, Any], timeout: float = 30.0
    ) -> Dict[str, Any]:
        """
        Invoke a tool on this provider.

        Thread-safe. Ensures provider is ready before invocation.

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

            # Record start event
            self._record_event(
                ToolInvocationRequested(
                    provider_id=self.provider_id,
                    tool_name=tool_name,
                    correlation_id=correlation_id,
                    arguments=arguments,
                )
            )

            start_time = time.time()

            try:
                response = self._client.call(
                    "tools/call",
                    {"name": tool_name, "arguments": arguments},
                    timeout=timeout,
                )

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

                logger.debug(
                    f"tool_invoked: {correlation_id}, "
                    f"provider={self.provider_id}, tool={tool_name}"
                )

                return result

            except ToolInvocationError:
                raise
            except Exception as e:
                self._health.record_failure()

                self._record_event(
                    ToolInvocationFailed(
                        provider_id=self.provider_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        error_message=str(e),
                        error_type=type(e).__name__,
                    )
                )

                logger.error(
                    f"tool_invocation_failed: {correlation_id}, "
                    f"provider={self.provider_id}, tool={tool_name}, error={e}"
                )

                raise ToolInvocationError(
                    self.provider_id,
                    str(e),
                    {"tool_name": tool_name, "correlation_id": correlation_id},
                ) from e

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
        """
        with self._lock:
            if self._state != ProviderState.READY:
                return False

            if not self._client or not self._client.is_alive():
                self._state = ProviderState.DEAD
                self._increment_version()
                return False

            try:
                start_time = time.time()
                response = self._client.call("tools/list", {}, timeout=5.0)

                if "error" in response:
                    raise Exception(response["error"].get("message", "unknown"))

                duration_ms = (time.time() - start_time) * 1000
                self._health.record_success()

                self._record_event(
                    HealthCheckPassed(provider_id=self.provider_id, duration_ms=duration_ms)
                )

                return True

            except Exception as e:
                self._health.record_failure()

                self._record_event(
                    HealthCheckFailed(
                        provider_id=self.provider_id,
                        consecutive_failures=self._health.consecutive_failures,
                        error_message=str(e),
                    )
                )

                logger.warning(f"health_check_failed: {self.provider_id}, error={e}")

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

    def maybe_shutdown_idle(self) -> bool:
        """
        Shutdown if idle past TTL.

        Thread-safe. Returns True if shutdown was performed.
        """
        with self._lock:
            if self._state != ProviderState.READY:
                return False

            idle_time = time.time() - self._last_used
            if idle_time > self._idle_ttl_s:
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

    def get_tool_names(self) -> List[str]:
        """Get list of available tool names."""
        with self._lock:
            return self._tools.list_names()

    def get_tools_dict(self) -> Dict[str, ToolSchema]:
        """Get tools as dictionary (for backward compatibility)."""
        with self._lock:
            return self._tools.to_dict()

    def to_status_dict(self) -> Dict[str, Any]:
        """Get status as dictionary (for registry.list)."""
        with self._lock:
            return {
                "provider": self.provider_id,
                "state": self._state.value,
                "alive": self._client is not None and self._client.is_alive(),
                "mode": self._mode,
                "image_or_command": self._image or self._command,
                "tools_cached": self._tools.list_names(),
                "health": self._health.to_dict(),
                "meta": dict(self._meta),
            }
