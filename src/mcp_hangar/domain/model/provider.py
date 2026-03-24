"""Provider aggregate root - the main domain entity."""

import threading
import time
from typing import Any, TYPE_CHECKING

from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...infrastructure.lock_hierarchy import TrackedLock

from ..contracts.log_buffer import IProviderLogBuffer
from ..contracts.metrics_publisher import IMetricsPublisher, NullMetricsPublisher
from ..value_objects.capabilities import ProviderCapabilities
from ..events import (
    HealthCheckFailed,
    HealthCheckPassed,
    ProviderDegraded,
    ProviderIdleDetected,
    ProviderStarted,
    ProviderStateChanged,
    ProviderStopped,
    ProviderUpdated,
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
from .provider_config import ProviderConfig
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
        container_command: list[str] | None = None,  # Override container entrypoint
        container_args: list[str] | None = None,  # Arguments for container command
        description: str | None = None,  # Description/preprompt for AI models
        # Pre-defined tools (allows visibility before provider starts)
        tools: list[dict[str, Any]] | None = None,
        # HTTP transport options (for remote mode)
        auth: dict[str, Any] | None = None,  # Authentication config
        tls: dict[str, Any] | None = None,  # TLS config
        http: dict[str, Any] | None = None,  # HTTP transport config
        # Dependencies
        metrics_publisher: IMetricsPublisher | None = None,
        log_buffer: IProviderLogBuffer | None = None,
        # Capability declarations (Phase 38)
        capabilities: ProviderCapabilities | None = None,
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
        self._container_command = container_command  # Override container entrypoint
        self._container_args = container_args  # Arguments for container command

        # HTTP transport configuration (for remote mode)
        self._auth_config = auth
        self._tls_config = tls
        self._http_config = http

        # Dependencies (Dependency Inversion Principle)
        self._metrics_publisher = metrics_publisher or NullMetricsPublisher()
        self._log_buffer = log_buffer

        # Capability declarations (Phase 38)
        self._capabilities = capabilities

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

        # Concurrent startup coordination
        # _ready_event is set initially (no one waiting). Cleared when a thread
        # begins startup (INITIALIZING), set again on success or failure.
        self._ready_event = threading.Event()
        self._ready_event.set()
        self._start_error: Exception | None = None

        # Tool refresh deduplication
        # Prevents concurrent invoke_tool() calls from issuing duplicate
        # tools/list RPCs when the tool is not yet in the catalog.
        self._refresh_in_progress = False

        # Thread safety
        # Lock hierarchy level: PROVIDER (10)
        # Safe to acquire after: (none - this is top level for domain)
        # Safe to acquire before: EVENT_BUS, EVENT_STORE, STDIO_CLIENT
        # I/O rule: Copy client reference under lock, do I/O outside lock
        self._lock = self._create_lock(provider_id)

    @classmethod
    def from_config(
        cls,
        config: ProviderConfig,
        metrics_publisher: "IMetricsPublisher | None" = None,
    ) -> "Provider":
        """Create Provider from ProviderConfig.

        This is the preferred way to create a Provider instance.
        Uses structured configuration instead of 21+ parameters.

        Args:
            config: Provider configuration dataclass.
            metrics_publisher: Optional metrics publisher for observability.

        Returns:
            Configured Provider instance.

        Example:
            config = ProviderConfig(
                provider_id="math",
                mode=ProviderMode.SUBPROCESS,
                subprocess=SubprocessConfig(command=["python", "-m", "math"]),
            )
            provider = Provider.from_config(config)
        """
        # Extract mode-specific configuration
        command = config.get_command()
        image = config.get_image()
        endpoint = config.get_endpoint()
        env = config.get_env()

        # Extract container-specific config
        volumes = None
        build = None
        resources = None
        network = "none"
        read_only = True
        user = None
        container_command = None
        container_args = None

        if config.container:
            volumes = config.container.volumes
            build = config.container.build
            resources = {
                "memory": config.container.resources.memory,
                "cpu": config.container.resources.cpu,
            }
            network = config.container.network
            read_only = config.container.read_only
            user = config.container.user
            container_command = config.container.command
            container_args = config.container.args

        # Extract remote-specific config
        auth = None
        tls = None
        http = None

        if config.remote:
            auth = config.remote.auth
            tls = config.remote.tls
            http = config.remote.http

        return cls(
            provider_id=config.provider_id,
            mode=config.mode,
            command=command,
            image=image,
            endpoint=endpoint,
            env=env,
            idle_ttl_s=config.idle_ttl,
            health_check_interval_s=config.health.check_interval,
            max_consecutive_failures=config.health.max_consecutive_failures,
            volumes=volumes,
            build=build,
            resources=resources,
            network=network,
            read_only=read_only,
            user=user,
            container_command=container_command,
            container_args=container_args,
            description=config.description,
            tools=config.tools,
            auth=auth,
            tls=tls,
            http=http,
            metrics_publisher=metrics_publisher,
            capabilities=getattr(config, "capabilities", None),
        )

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
    def state_snapshot(self) -> ProviderState:
        """Read current state without acquiring lock.

        Safe for callers that cannot acquire Provider lock due to lock hierarchy
        constraints (e.g., ProviderGroup at level 11 cannot acquire Provider at
        level 10).  Reading an enum attribute is atomic in CPython (GIL-protected
        single pointer read).  The value may be slightly stale, which is
        acceptable for health checks and rotation decisions.
        """
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

    @property
    def capabilities(self) -> ProviderCapabilities | None:
        """Declared capabilities for this provider."""
        return self._capabilities

    def set_log_buffer(self, buffer: "IProviderLogBuffer") -> None:
        """Inject or replace the log buffer for this provider.

        Intended for use by the bootstrap composition root to wire in the
        infrastructure log buffer after the provider has been constructed from
        config.  Safe to call before the provider is started.

        Args:
            buffer: The :class:`~mcp_hangar.domain.contracts.log_buffer.IProviderLogBuffer`
                implementation to use for capturing stderr output.
        """
        with self._lock:
            self._log_buffer = buffer

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

        Uses threading.Event for concurrent startup coordination:
        - First caller to find COLD/DEAD/DEGRADED becomes the "starter"
        - Subsequent callers finding INITIALIZING become "waiters"
        - Starter performs I/O outside lock, then signals waiters via Event
        - Failed startup propagates error to all waiters

        Raises:
            CannotStartProviderError: If backoff hasn't elapsed or startup times out
            ProviderStartError: If provider fails to start
        """
        should_start = False
        ready_event = None

        with self._lock:
            # Fast path -- already ready
            if self._state == ProviderState.READY:
                if self._client and self._client.is_alive():
                    return
                # Client died
                logger.warning(f"provider_dead: {self.provider_id}")
                self._state = ProviderState.DEAD

            # Another thread is starting: become a waiter
            if self._state == ProviderState.INITIALIZING:
                ready_event = self._ready_event
            elif self._state in (
                ProviderState.COLD,
                ProviderState.DEAD,
                ProviderState.DEGRADED,
            ):
                # Check if we can start
                can_start, reason, time_left = self._can_start()
                if not can_start:
                    raise CannotStartProviderError(
                        self.provider_id,
                        f"backoff not elapsed, retry in {time_left:.1f}s",
                        time_left,
                    )
                # We are the starter: transition and prepare event
                self._transition_to(ProviderState.INITIALIZING)
                self._ready_event = threading.Event()  # Fresh event for this attempt
                self._start_error = None
                ready_event = self._ready_event
                should_start = True
            else:
                return  # Unknown state, no-op

        if should_start:
            # Path A: We are the starter -- all I/O outside lock
            self._start()
        else:
            # Path B: We are a waiter -- wait for starter to finish
            if not ready_event.wait(timeout=30.0):
                raise CannotStartProviderError(
                    self.provider_id,
                    "startup_timeout: timed out waiting for provider to start",
                    30.0,
                )
            if self._start_error:
                raise ProviderStartError(
                    provider_id=self.provider_id,
                    reason=str(self._start_error),
                )

    def _start(self) -> None:
        """
        Start provider process with I/O outside lock.

        Called after ensure_ready() has set state to INITIALIZING and
        released the lock. Performs subprocess launch and MCP handshake
        outside the lock, then reacquires to finalize or handle failure.

        Signals _ready_event on completion (success or failure) to wake
        any concurrent waiters.
        """
        start_time = time.time()
        cold_start_time = self._begin_cold_start_tracking()
        client = None  # Track client for diagnostics on failure

        try:
            # I/O outside lock: subprocess launch and MCP handshake
            client = self._create_client()
            self._perform_mcp_handshake(client)

            # Reacquire lock to finalize state
            with self._lock:
                self._finalize_start(client, start_time)
                self._end_cold_start_tracking(cold_start_time, success=True)
                self._ready_event.set()  # Wake waiters: success

        except ProviderStartError as e:
            with self._lock:
                self._end_cold_start_tracking(cold_start_time, success=False)
                self._handle_start_failure(e)
                self._start_error = e
                self._ready_event.set()  # Wake waiters: failure
            raise
        except Exception as e:  # noqa: BLE001 -- fault-barrier: wrap unexpected startup errors in ProviderStartError for callers
            # Collect diagnostics from client if available
            diagnostics = self._collect_startup_diagnostics(client) if client else {}

            with self._lock:
                self._end_cold_start_tracking(cold_start_time, success=False)
                self._handle_start_failure(e)
                start_error = ProviderStartError(
                    provider_id=self.provider_id,
                    reason=str(e),
                    stderr=diagnostics.get("stderr"),
                    exit_code=diagnostics.get("exit_code"),
                    suggestion=diagnostics.get("suggestion"),
                )
                self._start_error = start_error
                self._ready_event.set()  # Wake waiters: failure

            raise start_error from e

    def _begin_cold_start_tracking(self) -> float | None:
        """Begin tracking cold start metrics. Returns start timestamp."""
        try:
            self._metrics_publisher.begin_cold_start(self.provider_id)
            return time.time()
        except Exception:  # noqa: BLE001 -- fault-barrier: metrics must not crash provider startup
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
        except Exception:  # noqa: BLE001 -- fault-barrier: metrics must not crash provider startup
            pass

    def _create_client(self) -> Any:
        """Create and return the appropriate client based on mode."""
        from ..services.provider_launcher import get_launcher

        launcher = get_launcher(self._mode.value)
        config = self._get_launch_config()
        client = launcher.launch(**config)

        # Start live stderr-reader thread if a log buffer is configured and the
        # client has a process with a stderr pipe (subprocess/docker/container modes).
        if self._log_buffer is not None:
            self._start_stderr_reader(client)

        return client

    def _start_stderr_reader(self, client: Any) -> None:
        """Spawn a daemon thread that reads stderr lines into the log buffer.

        The thread iterates over ``client.process.stderr`` line-by-line until
        EOF (process exit), appending each line to ``self._log_buffer``.  It
        is a daemon thread so it never blocks interpreter shutdown.

        This method is a no-op when the process has no stderr pipe (e.g., when
        stderr is ``DEVNULL`` or the client is an HTTP transport with no process).

        Args:
            client: The newly created client (``StdioClient`` or similar).
        """
        process = getattr(client, "process", None)
        stderr_pipe = getattr(process, "stderr", None) if process is not None else None
        if stderr_pipe is None:
            return

        from ..value_objects.log import LogLine

        provider_id = self.provider_id
        # self._log_buffer is guaranteed non-None here: _create_client guards with `if self._log_buffer is not None`
        log_buffer: IProviderLogBuffer = self._log_buffer  # type: ignore[assignment]

        def _reader() -> None:
            try:
                for raw_line in stderr_pipe:
                    content = raw_line.rstrip("\n")
                    log_buffer.append(
                        LogLine(
                            provider_id=provider_id,
                            stream="stderr",
                            content=content,
                        )
                    )
            except Exception:  # noqa: BLE001 -- fault-barrier: reader thread must not crash on pipe error
                pass

        t = threading.Thread(target=_reader, daemon=True, name=f"stderr-reader-{provider_id}")
        t.start()

    def _get_launch_config(self) -> dict[str, Any]:
        """Get launch configuration for the current mode."""
        if self._mode == ProviderMode.SUBPROCESS:
            return {"command": self._command, "env": self._env}

        if self._mode == ProviderMode.DOCKER:
            return {
                "image": self._image,
                "command": self._container_command,
                "args": self._container_args,
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
                "command": self._container_command,
                "args": self._container_args,
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
        except Exception:  # noqa: BLE001 -- fault-barrier: diagnostics logging must not mask startup errors
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
            except Exception:  # noqa: BLE001 -- fault-barrier: diagnostics logging must not mask startup errors
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
            except Exception:  # noqa: BLE001 -- fault-barrier: cleanup must not mask original startup error
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

        Uses a multi-lock-cycle pattern to avoid holding the lock during I/O:
        - Lock cycle 1: Ensure ready, check tool exists, decide if refresh needed
        - Refresh phase (outside lock): tools/list RPC if needed
        - Lock cycle 2 (if refreshed): Apply results, re-check tool, prepare invocation
        - Invocation phase (outside lock): tools/call RPC
        - Lock cycle 3: Update state based on result

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

        # Lock cycle 1: Validation, ensure ready, check tool, maybe prepare refresh
        needs_refresh = False
        tool_found = False
        client = None
        with self._lock:
            self.ensure_ready()

            if self._tools.has(tool_name):
                tool_found = True
            elif not self._refresh_in_progress:
                # We will perform the refresh -- claim the slot
                self._refresh_in_progress = True
                needs_refresh = True
            # else: another thread is refreshing, we skip and re-check later

            if tool_found:
                # Tool exists, proceed directly to invocation
                self._health._total_invocations += 1
                client = self._client
                self._record_event(
                    ToolInvocationRequested(
                        provider_id=self.provider_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        arguments=arguments,
                    )
                )

        # Refresh phase (outside lock): tools/list RPC
        if needs_refresh:
            refresh_error = None
            refresh_result = None
            # Copy client reference for I/O -- already validated as READY above
            with self._lock:
                refresh_client = self._client

            try:
                refresh_result = refresh_client.call("tools/list", {}, timeout=5.0)
            except (OSError, TimeoutError) as e:
                refresh_error = e
                logger.warning(f"tool_refresh_failed: {self.provider_id}, error={e}")

            # Lock cycle 2: Apply refresh results, clear flag, re-check tool
            with self._lock:
                self._refresh_in_progress = False

                if refresh_error is None and refresh_result and "result" in refresh_result:
                    tool_list = refresh_result.get("result", {}).get("tools", [])
                    self._tools.update_from_list(tool_list)

                if not self._tools.has(tool_name):
                    raise ToolNotFoundError(self.provider_id, tool_name)

                tool_found = True
                self._health._total_invocations += 1
                client = self._client
                self._record_event(
                    ToolInvocationRequested(
                        provider_id=self.provider_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        arguments=arguments,
                    )
                )
        elif not tool_found:
            # Another thread is refreshing but tool still not found -- raise
            raise ToolNotFoundError(self.provider_id, tool_name)

        # Invocation phase (outside lock): tools/call RPC
        start_time = time.time()
        response = None
        invocation_error = None

        try:
            response = client.call(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=timeout,
            )
        except (OSError, TimeoutError) as e:
            invocation_error = e

        # Lock cycle 3: Update state based on result
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
        """Refresh tool catalog from provider.

        Note: This performs I/O (tools/list RPC). Callers should prefer the
        two-lock-cycle pattern in invoke_tool() which performs the RPC outside
        the lock. This method is retained for internal use but should NOT be
        called while holding the provider lock.
        """
        if not self._client or not self._client.is_alive():
            return

        try:
            tools_resp = self._client.call("tools/list", {}, timeout=5.0)
            if "result" in tools_resp:
                tool_list = tools_resp.get("result", {}).get("tools", [])
                self._tools.update_from_list(tool_list)
        except (OSError, TimeoutError) as e:
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
        except (OSError, TimeoutError) as e:
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
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown cleanup must not propagate
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

    def to_config_dict(self) -> dict[str, Any]:
        """Return YAML-compatible config spec dict.

        Returns the minimal representation for round-trip:
        load_config(to_config_dict()) produces an equivalent Provider.

        Returns:
            Dictionary of provider configuration fields, omitting optional
            fields that are empty or equal to their defaults.
        """
        spec: dict[str, Any] = {
            "mode": self._mode.value,
            "idle_ttl_s": self._idle_ttl.seconds,
            "health_check_interval_s": self._health_check_interval.seconds,
        }
        if self._command:
            spec["command"] = list(self._command)
        if self._image:
            spec["image"] = self._image
        if self._endpoint:
            spec["endpoint"] = self._endpoint
        if self._env:
            spec["env"] = dict(self._env)
        if self._description:
            spec["description"] = self._description
        if self._volumes:
            spec["volumes"] = list(self._volumes)
        if self._network and self._network != "none":
            spec["network"] = self._network
        if not self._read_only:
            spec["read_only"] = False
        if self._capabilities is not None:
            spec["capabilities"] = self._capabilities  # Phase 38: serialization in future plan
        return spec

    def update_config(
        self,
        description: str | None = None,
        env: dict[str, str] | None = None,
        idle_ttl_s: int | None = None,
        health_check_interval_s: int | None = None,
    ) -> None:
        """Update mutable configuration fields and record a domain event.

        Only non-None arguments are applied; fields not passed are unchanged.
        Acquires self._lock internally -- do NOT call under an external lock.

        Args:
            description: New human-readable description (optional).
            env: New environment variable dict (replaces existing, optional).
            idle_ttl_s: New idle TTL in seconds (optional).
            health_check_interval_s: New health check interval in seconds (optional).
        """
        with self._lock:
            if description is not None:
                self._description = description
            if env is not None:
                self._env = dict(env)
            if idle_ttl_s is not None:
                self._idle_ttl = IdleTTL(idle_ttl_s)
            if health_check_interval_s is not None:
                self._health_check_interval = HealthCheckInterval(health_check_interval_s)
        self._record_event(ProviderUpdated(provider_id=self.provider_id, source="api"))
