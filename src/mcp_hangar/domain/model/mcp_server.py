"""McpServer aggregate root - the main domain entity."""

import threading
import time
from typing import Any, TYPE_CHECKING, cast

from ...logging_config import get_logger
from ...protocol import HANGAR_CLIENT_INFO, SESSION_TERMINATED_REASON, SUPPORTED_PROTOCOL_VERSION

if TYPE_CHECKING:
    from ..policies.egress_l7 import L7Policy

from ..contracts.log_buffer import IMcpServerLogBuffer
from ...lock_hierarchy import LockLevel, TrackedLock
from ..contracts.launcher import TransportClient
from ..contracts.metrics_publisher import IMetricsPublisher, get_default_metrics_publisher
from ..value_objects.capabilities import McpServerCapabilities, ViolationSeverity, ViolationType
from ..events import (
    CapabilityViolationDetected,
    DomainEvent,
    EgressPolicyViolationObserved,
    HealthCheckFailed,
    HealthCheckPassed,
    McpServerDegraded,
    McpServerIdleDetected,
    McpServerStarted,
    McpServerStateChanged,
    McpServerStopped,
    McpServerUpdated,
    ToolInvocationCompleted,
    ToolInvocationFailed,
    ToolInvocationRequested,
)
from ..exceptions import (
    CannotStartMcpServerError,
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    InvalidStateTransitionError,
    McpServerNotHereError,
    McpServerStartError,
    ToolInvocationError,
    ToolNotFoundError,
)
from ..services.error_diagnostics import collect_startup_diagnostics
from ..value_objects import CorrelationId, HealthCheckInterval, IdleTTL, McpServerId, McpServerMode, McpServerState
from ..value_objects.provenance import Provenance
from .aggregate import AggregateRoot
from .health_tracker import HealthTracker
from .mcp_server_config import McpServerConfig
from .tool_catalog import ToolCatalog, ToolSchema

logger = get_logger(__name__)


# SUPPORTED_PROTOCOL_VERSION / HANGAR_CLIENT_INFO live in the leaf `protocol`
# module (re-exported above) so the transport clients can share them without a
# domain -> transport import. Re-export keeps existing import sites working.

#: The generation whose `_meta` envelope a connection must have negotiated before
#: Hangar may keep stamping it. ISO dates compare correctly as strings.
_MODERN_PROTOCOL_VERSION = "2026-07-28"

# JSON-RPC "method not found". A stateless MCP server (SEP-2575) removed the
# `initialize` handler and answers with this code; we treat it as "this upstream
# is stateless, skip the handshake" rather than a startup failure.
_JSONRPC_METHOD_NOT_FOUND = -32601


# Valid state transitions
VALID_TRANSITIONS = {
    McpServerState.COLD: {McpServerState.INITIALIZING},
    McpServerState.INITIALIZING: {
        McpServerState.READY,
        McpServerState.DEAD,
        McpServerState.DEGRADED,
    },
    McpServerState.READY: {
        McpServerState.COLD,
        McpServerState.DEAD,
        McpServerState.DEGRADED,
    },
    McpServerState.DEGRADED: {McpServerState.INITIALIZING, McpServerState.COLD},
    McpServerState.DEAD: {McpServerState.INITIALIZING, McpServerState.DEGRADED},
}


class McpServer(AggregateRoot):
    """
    McpServer aggregate root.

    Manages the complete lifecycle of an MCP mcp_server including:
    - State machine with valid transitions
    - Health tracking and circuit breaker logic
    - Tool catalog management
    - Process/client management

    All public operations are thread-safe using internal locking.
    """

    def __init__(
        self,
        mcp_server_id: str,
        mode: str | McpServerMode,  # Accept both string and enum
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
        # Pre-defined tools (allows visibility before mcp_server starts)
        tools: list[dict[str, Any]] | None = None,
        # HTTP transport options (for remote mode)
        auth: dict[str, Any] | None = None,  # Authentication config
        tls: dict[str, Any] | None = None,  # TLS config
        http: dict[str, Any] | None = None,  # HTTP transport config
        # Dependencies
        metrics_publisher: IMetricsPublisher | None = None,
        log_buffer: IMcpServerLogBuffer | None = None,
        # Capability declarations (Phase 38)
        capabilities: McpServerCapabilities | None = None,
        # L7 egress policy (MCPEgressPolicy); None = no enforcement
        l7_policy: "L7Policy | None" = None,
        # SSRF provenance policy for a remote endpoint (see below).
        provenance: Provenance = Provenance.HUMAN,
        runtime_addresses: frozenset[str] | None = None,
        # Whether the connect-time SSRF guard applies to this server. On only for
        # endpoints the registration check guarded (created through the command
        # handler); off for config-file / directly-built servers so an
        # intentionally private endpoint is not newly refused at connect.
        enforce_ssrf: bool = False,
    ):
        super().__init__()

        # Identity
        self._id = McpServerId(mcp_server_id)

        # Mode - normalize to McpServerMode enum (container -> docker)
        self._mode = McpServerMode.normalize(mode)

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

        # SSRF policy for a remote endpoint, carried to the transport so the
        # connect-time re-check (DNS-rebinding defence) applies the SAME policy
        # the registration-time `validate_no_ssrf` used. `validate_no_ssrf` runs
        # once, when the server is registered; the client that actually connects
        # is created lazily on first use (and rebuilt from a snapshot after a
        # restart), so these have to travel with the aggregate to reach it.
        # HUMAN + None is the strict default: a construction path that does not
        # set them (a config-file server, an older snapshot) gets the human
        # policy, which is the safe direction to fail.
        self._provenance = provenance
        self._runtime_addresses = runtime_addresses
        self._enforce_ssrf = enforce_ssrf

        # Dependencies (Dependency Inversion Principle)
        self._metrics_publisher = metrics_publisher or get_default_metrics_publisher()
        self._log_buffer = log_buffer

        # Capability declarations (Phase 38)
        self._capabilities = capabilities

        # L7 egress policy (MCPEgressPolicy). None means no L7 enforcement; when
        # set, invoke_tool evaluates every tool call against it.
        self._l7_policy = l7_policy

        # State
        self._state = McpServerState.COLD
        self._health = HealthTracker(max_consecutive_failures=max_consecutive_failures)
        self._tools = ToolCatalog()
        # Typed by the port rather than Any: the domain's whole use of a launched
        # connection is is_alive/close/call, and until TransportClient existed the
        # real type lived in a comment where nothing could check it.
        self._client: TransportClient | None = None
        self._meta: dict[str, Any] = {}
        self._last_used: float = 0.0

        # Pre-load tools from configuration (allows visibility before start).
        #
        # A statically-configured `tools:` list is a PRE-START VISIBILITY
        # PROJECTION only. On start, the provider's dynamic `tools/list` is
        # authoritative and REPLACES this projection (see
        # _perform_mcp_handshake). A statically-listed tool that the provider
        # does not return will be uncallable and raise ToolNotFoundError at
        # invocation. We retain the predefined names so start can warn on any
        # unconfirmed static tool (observability only -- no behavior change).
        self._tools_predefined = False
        self._tools_predefined_names: frozenset[str] = frozenset()
        if tools:
            self._tools.update_from_list(tools)
            self._tools_predefined = True
            self._tools_predefined_names = frozenset(self._tools.list_names())

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
        self._lock = self._create_lock(mcp_server_id)

    @classmethod
    def from_config(
        cls,
        config: McpServerConfig,
        metrics_publisher: "IMetricsPublisher | None" = None,
    ) -> "McpServer":
        """Create McpServer from McpServerConfig.

        This is the preferred way to create a McpServer instance.
        Uses structured configuration instead of 21+ parameters.

        Args:
            config: McpServer configuration dataclass.
            metrics_publisher: Optional metrics publisher for observability.

        Returns:
            Configured McpServer instance.

        Example:
            config = McpServerConfig(
                mcp_server_id="math",
                mode=McpServerMode.SUBPROCESS,
                subprocess=SubprocessConfig(command=["python", "-m", "math"]),
            )
            mcp_server = McpServer.from_config(config)
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
            mcp_server_id=config.mcp_server_id,
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
    def _create_lock(mcp_server_id: str) -> TrackedLock:
        """Create the aggregate's lock, registered in the global ordering.

        The tracking is not optional: a bare RLock here would take this
        aggregate out of the hierarchy and let a deadlock through undetected.
        This used to sit in a `try/except ImportError` that fell back to exactly
        that -- an except branch that could never run, since the module ships in
        the package, quietly guarding against a failure mode that would have
        been silent if it ever did.
        """
        return TrackedLock(LockLevel.PROVIDER, f"McpServer:{mcp_server_id}")

    # --- Properties ---

    @property
    def id(self) -> McpServerId:
        """McpServer identifier."""
        return self._id

    @property
    def mcp_server_id(self) -> str:
        """McpServer identifier as string (for backward compatibility)."""
        return str(self._id)

    @property
    def mode(self) -> McpServerMode:
        """McpServer mode enum."""
        return self._mode

    @property
    def l7_policy(self) -> "L7Policy | None":
        """The L7 egress policy enforced on tool calls, or None if unset."""
        return self._l7_policy

    def set_l7_policy(self, policy: "L7Policy | None") -> None:
        """Attach, replace, or clear the L7 egress policy.

        Used to populate the policy from its source (the operator's compiled
        MCPEgressPolicy). Clearing (None) disables L7 enforcement for this server.
        """
        self._l7_policy = policy

    @property
    def mode_str(self) -> str:
        """McpServer mode as string (for backward compatibility)."""
        return self._mode.value

    @property
    def description(self) -> str | None:
        """McpServer description for AI models."""
        return self._description

    @property
    def state(self) -> McpServerState:
        """Current mcp_server state."""
        with self._lock:
            return self._state

    @property
    def state_snapshot(self) -> McpServerState:
        """Read current state without acquiring lock.

        Safe for callers that cannot acquire McpServer lock due to lock hierarchy
        constraints (e.g., McpServerGroup at level 11 cannot acquire McpServer at
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
        """Check if mcp_server has any tools registered (predefined or discovered)."""
        return self._tools.count() > 0

    @property
    def tools_predefined(self) -> bool:
        """Check if tools were predefined in configuration (no startup needed for visibility)."""
        return self._tools_predefined

    @property
    def is_alive(self) -> bool:
        """Check if mcp_server client is alive."""
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
        """Check if mcp_server has been idle longer than TTL."""
        with self._lock:
            if self._state != McpServerState.READY:
                return False
            if self._last_used == 0:
                return False
            return self.idle_time > self._idle_ttl.seconds

    @property
    def meta(self) -> dict[str, Any]:
        """McpServer metadata."""
        with self._lock:
            return dict(self._meta)

    @property
    def lock(self) -> "TrackedLock | threading.RLock":
        """Get the internal lock (for backward compatibility)."""
        return self._lock

    @property
    def capabilities(self) -> McpServerCapabilities | None:
        """Declared capabilities for this mcp_server."""
        return self._capabilities

    def set_log_buffer(self, buffer: "IMcpServerLogBuffer") -> None:
        """Inject or replace the log buffer for this mcp_server.

        Intended for use by the bootstrap composition root to wire in the
        infrastructure log buffer after the mcp_server has been constructed from
        config.  Safe to call before the mcp_server is started.

        Args:
            buffer: The :class:`~mcp_hangar.domain.contracts.log_buffer.IMcpServerLogBuffer`
                implementation to use for capturing stderr output.
        """
        with self._lock:
            self._log_buffer = buffer

    # --- State Management ---

    def _transition_to(self, new_state: McpServerState) -> None:
        """
        Transition to a new state (must hold lock).

        Validates the transition is valid according to state machine rules.
        Records a McpServerStateChanged event.
        """
        if new_state == self._state:
            return

        if new_state not in VALID_TRANSITIONS.get(self._state, set()):
            raise InvalidStateTransitionError(self.mcp_server_id, str(self._state.value), str(new_state.value))

        old_state = self._state
        self._state = new_state
        self._increment_version()

        self._record_event(
            McpServerStateChanged(
                mcp_server_id=self.mcp_server_id,
                old_state=str(old_state.value),
                new_state=str(new_state.value),
            )
        )

    def _can_start(self) -> tuple:
        """
        Check if mcp_server can be started (must hold lock).

        Returns: (can_start, reason, time_until_retry)
        """
        if self._state == McpServerState.READY:
            if self._client and self._client.is_alive():
                return True, "already_ready", 0

        if self._state == McpServerState.DEGRADED:
            if not self._health.can_retry():
                time_left = self._health.time_until_retry()
                return False, "backoff_not_elapsed", time_left

        return True, "", 0

    # --- Business Operations ---

    def ensure_ready(self) -> None:
        """
        Ensure mcp_server is in READY state, starting if necessary.

        Thread-safe. Blocks until ready or raises exception.

        Uses threading.Event for concurrent startup coordination:
        - First caller to find COLD/DEAD/DEGRADED becomes the "starter"
        - Subsequent callers finding INITIALIZING become "waiters"
        - Starter performs I/O outside lock, then signals waiters via Event
        - Failed startup propagates error to all waiters

        Raises:
            CannotStartMcpServerError: If backoff hasn't elapsed or startup times out
            McpServerStartError: If mcp_server fails to start
        """
        should_start = False
        ready_event = None

        with self._lock:
            # Fast path -- already ready
            if self._state == McpServerState.READY:
                if self._client and self._client.is_alive():
                    return
                # Client died
                logger.warning(f"mcp_server_dead: {self.mcp_server_id}")
                self._state = McpServerState.DEAD

            # Another thread is starting: become a waiter
            if self._state == McpServerState.INITIALIZING:
                ready_event = self._ready_event
            elif self._state in (
                McpServerState.COLD,
                McpServerState.DEAD,
                McpServerState.DEGRADED,
            ):
                # Check if we can start
                can_start, reason, time_left = self._can_start()
                if not can_start:
                    raise CannotStartMcpServerError(
                        self.mcp_server_id,
                        f"backoff not elapsed, retry in {time_left:.1f}s",
                        time_left,
                    )
                # We are the starter: transition and prepare event
                self._transition_to(McpServerState.INITIALIZING)
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
                raise CannotStartMcpServerError(
                    self.mcp_server_id,
                    "startup_timeout: timed out waiting for mcp_server to start",
                    30.0,
                )
            if self._start_error:
                raise McpServerStartError(
                    mcp_server_id=self.mcp_server_id,
                    reason=str(self._start_error),
                )

    def _start(self) -> None:
        """
        Start mcp_server process with I/O outside lock.

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

        except McpServerNotHereError as e:
            # Not a start failure: this replica is the wrong one to ask. Wrapping
            # it in McpServerStartError sent the caller a 500 about a gateway
            # behaving exactly as designed. Recorded and re-raised as itself so
            # the API can answer 409 and name the instance to ask instead.
            with self._lock:
                self._end_cold_start_tracking(cold_start_time, success=False)
                self._handle_start_failure(e)
                self._start_error = e
                self._ready_event.set()  # Wake waiters: failure
            raise
        except McpServerStartError as e:
            with self._lock:
                self._end_cold_start_tracking(cold_start_time, success=False)
                self._handle_start_failure(e)
                self._start_error = e
                self._ready_event.set()  # Wake waiters: failure
            raise
        except Exception as e:  # noqa: BLE001 -- fault-barrier: wrap unexpected startup errors in McpServerStartError for callers
            # Collect diagnostics from client if available
            diagnostics = self._collect_startup_diagnostics(client) if client else {}

            with self._lock:
                self._end_cold_start_tracking(cold_start_time, success=False)
                self._handle_start_failure(e)
                start_error = McpServerStartError(
                    mcp_server_id=self.mcp_server_id,
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
            self._metrics_publisher.begin_cold_start(self.mcp_server_id)
            return time.time()
        except Exception:  # noqa: BLE001 -- fault-barrier: metrics must not crash mcp_server startup
            return None

    def _end_cold_start_tracking(self, start_time: float | None, success: bool) -> None:
        """End cold start tracking and record metrics."""
        if start_time is None:
            return
        try:
            if success:
                duration = time.time() - start_time
                self._metrics_publisher.record_cold_start(self.mcp_server_id, duration, self._mode.value)
            self._metrics_publisher.end_cold_start(self.mcp_server_id)
        except Exception:  # noqa: BLE001 -- fault-barrier: metrics must not crash mcp_server startup
            pass

    def _create_client(self) -> Any:
        """Create and return the appropriate client based on mode."""
        from mcp_hangar.infrastructure.launchers import get_launcher

        launcher: Any = get_launcher(self._mode.value)
        config = self._get_launch_config()
        client = launcher.launch(**config)

        # stdio transports start unlabeled; tag them so their message metrics
        # carry this server's ID (HTTP clients are labeled at construction).
        if getattr(client, "mcp_server_id", "unset") is None:
            client.mcp_server_id = str(self.mcp_server_id)

        # Start live stderr-reader thread if a log buffer is configured and the
        # client has a process with a stderr pipe (subprocess/docker/container modes).
        if self._log_buffer is not None:
            self._start_stderr_reader(client)

        self._metrics_publisher.set_connection_active(self.mcp_server_id, True)

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

        from ...redactor import get_default_redactor
        from ..value_objects.log import LogLine

        mcp_server_id = self.mcp_server_id
        # self._log_buffer is guaranteed non-None here: _create_client guards with `if self._log_buffer is not None`
        log_buffer: IMcpServerLogBuffer = self._log_buffer  # type: ignore[assignment]
        redactor = get_default_redactor()

        def _reader() -> None:
            try:
                for raw_line in stderr_pipe:
                    # Redact at the source: MCP-server stderr routinely contains
                    # tokens/keys/connection strings, and this content is served
                    # verbatim by the /logs API. Scrub before it ever enters the
                    # buffer so every downstream consumer is safe.
                    content = redactor.redact(raw_line.rstrip("\n"))
                    log_buffer.append(
                        LogLine(
                            mcp_server_id=mcp_server_id,
                            stream="stderr",
                            content=content,
                        )
                    )
            except Exception:  # noqa: BLE001 -- fault-barrier: reader thread must not crash on pipe error
                pass

        t = threading.Thread(target=_reader, daemon=True, name=f"stderr-reader-{mcp_server_id}")
        t.start()

    def _get_launch_config(self) -> dict[str, Any]:
        """Get launch configuration for the current mode."""
        if self._mode == McpServerMode.SUBPROCESS:
            return {"command": self._command, "env": self._env}

        if self._mode == McpServerMode.DOCKER:
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
                "mcp_server_id": self.mcp_server_id,
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
                "mcp_server_id": self.mcp_server_id,
            }

        if self._mode == McpServerMode.REMOTE:
            return {
                "endpoint": self._endpoint,
                "auth_config": self._auth_config,
                "tls_config": self._tls_config,
                "http_config": self._http_config,
                # Carried to the transport so the connect-time SSRF re-check uses
                # the same policy the registration check did.
                "provenance": self._provenance,
                "runtime_addresses": self._runtime_addresses,
                "enforce_ssrf": self._enforce_ssrf,
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
            logger.info(f"Built image for {self.mcp_server_id}: {image}")
            return image

        if not self._image:
            raise McpServerStartError(
                self.mcp_server_id,
                "Container mode requires 'image' or 'build.dockerfile'",
            )
        return self._image

    def _is_session_terminated(self, response: Any) -> bool:
        """Did the upstream reject our transport session?

        Keyed on the machine-readable discriminator rather than the message,
        which is prose.
        """
        if not isinstance(response, dict):
            return False
        error = response.get("error")
        if not isinstance(error, dict):
            return False
        data = error.get("data")
        return isinstance(data, dict) and data.get("reason") == SESSION_TERMINATED_REASON

    def _call_with_session_recovery(
        self, client: Any, method: str, params: dict[str, Any], timeout: float | None = None
    ) -> Any:
        """Call *method*, re-handshaking once if the upstream lost our session.

        An upstream that restarts forgets every session it issued. Before this,
        the client kept presenting the dead id and every later call answered 404
        forever -- while readiness still reported the gateway healthy, so nothing
        restarted it and nothing alerted. Recovery required restarting the
        gateway itself (#651).

        The retry is deliberately once. A second failure is not a stale session,
        it is an upstream that will not hold one, and looping would turn a
        recoverable blip into an unbounded retry against a sick backend.

        Failing to renegotiate records a health failure; succeeding does not.
        That ordering is the decision: a session lost to an ordinary restart is
        not evidence of an unhealthy upstream, and marking it so would pull the
        pod out of its Service for something that just healed itself.
        """
        response = client.call(method, params, timeout=timeout)
        if not self._is_session_terminated(response):
            return response

        logger.warning(
            "mcp_session_terminated_renegotiating",
            mcp_server_id=self.mcp_server_id,
            method=method,
        )
        try:
            self._perform_mcp_handshake(client)
        except Exception as exc:  # noqa: BLE001 -- any handshake failure is the same verdict here
            self._health.record_failure()
            logger.error(
                "mcp_session_renegotiation_failed",
                mcp_server_id=self.mcp_server_id,
                method=method,
                error=str(exc),
            )
            return response

        logger.info("mcp_session_renegotiated", mcp_server_id=self.mcp_server_id, method=method)
        return client.call(method, params, timeout=timeout)

    def _perform_mcp_handshake(self, client: Any) -> None:
        """Perform the MCP startup handshake and tools/list discovery.

        Legacy upstreams answer ``initialize``; stateless upstreams (SEP-2575)
        removed it and reply method-not-found, which is tolerated rather than
        treated as a startup failure. Either way we then discover tools.
        """
        # Note: timeout is handled by the client's configuration
        # (StdioClient: 15s default, HttpClient: configured read_timeout)
        init_resp = client.call(
            "initialize",
            {
                "protocolVersion": SUPPORTED_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": dict(HANGAR_CLIENT_INFO),
            },
        )

        init_error = init_resp.get("error")
        if init_error is not None and init_error.get("code") == _JSONRPC_METHOD_NOT_FOUND:
            # Stateless upstream (SEP-2575): no initialize handshake. Expected.
            # Keep the modern envelope -- it is the only way such an upstream
            # learns the protocol version and client info at all.
            logger.info("mcp_handshake_stateless_upstream", mcp_server_id=self.mcp_server_id)
        elif init_error is not None:
            error_msg = init_error.get("message", "unknown")
            self._log_client_error(client, error_msg)

            # Collect full diagnostics for user-friendly error
            diagnostics = self._collect_startup_diagnostics(client)
            raise McpServerStartError(
                mcp_server_id=self.mcp_server_id,
                reason=f"MCP initialization failed: {error_msg}",
                stderr=diagnostics.get("stderr"),
                exit_code=diagnostics.get("exit_code"),
                suggestion=diagnostics.get("suggestion")
                or "Check mcp_server logs and ensure it implements the MCP protocol correctly.",
            )

        if init_error is None:
            # A handshake happened, so this connection belongs to whichever era
            # the upstream negotiated -- and from mcp 2.0.0 a legacy connection
            # REJECTS the 2026-07-28 `_meta` envelope on every later request
            # (-32600), which reads as a hang: discovery fails, the cold start
            # never completes, the batch times out. Stop stamping it unless the
            # upstream actually negotiated the modern generation.
            negotiated = (init_resp.get("result") or {}).get("protocolVersion")
            if isinstance(negotiated, str) and negotiated < _MODERN_PROTOCOL_VERSION:
                client.modern_envelope = False
                logger.debug(
                    "upstream_legacy_era",
                    mcp_server_id=self.mcp_server_id,
                    negotiated_protocol_version=negotiated,
                )

        # Discover tools
        tools_resp = client.call("tools/list", {})
        if "error" in tools_resp:
            error_msg = tools_resp["error"].get("message", "unknown")
            diagnostics = self._collect_startup_diagnostics(client)
            raise McpServerStartError(
                mcp_server_id=self.mcp_server_id,
                reason=f"Failed to list tools: {error_msg}",
                stderr=diagnostics.get("stderr"),
                exit_code=diagnostics.get("exit_code"),
                suggestion=diagnostics.get("suggestion")
                or "McpServer started but tools/list failed. Check mcp_server implementation.",
            )

        tool_list = tools_resp.get("result", {}).get("tools", [])
        self._tools.update_from_list(tool_list)

        # Observability: the dynamic tools/list above is authoritative and has
        # just replaced any static pre-start projection. Surface the silent
        # mismatch when a statically pre-configured tool is not confirmed by
        # the provider -- such a tool is now uncallable and will raise
        # ToolNotFoundError at invocation. This is a WARNING only; it does not
        # change behavior (making static authoritative is a separate decision).
        if self._tools_predefined_names:
            missing = sorted(name for name in self._tools_predefined_names if not self._tools.has(name))
            if missing:
                logger.warning(
                    f"static_tools_unconfirmed: {self.mcp_server_id}, tools={missing} -- "
                    "statically-configured tool(s) not returned by the provider's tools/list; "
                    "they are uncallable and will raise ToolNotFoundError at invocation"
                )

    def _log_client_error(self, client: Any, error_msg: str) -> None:
        """Log detailed error info including stderr and exit code for debugging."""
        proc = getattr(client, "process", None)
        if not proc:
            return

        # Log exit code
        try:
            rc = proc.poll()
            if rc is not None:
                logger.error(f"mcp_server_process_exit_code: {rc}")
        except Exception:  # noqa: BLE001 -- fault-barrier: diagnostics logging must not mask startup errors
            pass

        # Try to capture stderr (may already be captured by StdioClient)
        last_stderr = getattr(client, "_last_stderr", None)
        if last_stderr:
            logger.error(f"mcp_server_stderr: {last_stderr}")
            return

        # Fallback: try to read stderr directly
        stderr = getattr(proc, "stderr", None)
        if stderr:
            try:
                err_bytes = stderr.read()
                if err_bytes:
                    err_text = (err_bytes if isinstance(err_bytes, str) else err_bytes.decode(errors="replace")).strip()
                    if err_text:
                        logger.error(f"mcp_server_stderr: {err_text}")
            except Exception:  # noqa: BLE001 -- fault-barrier: diagnostics logging must not mask startup errors
                pass

    def _collect_startup_diagnostics(self, client: Any) -> dict[str, Any]:
        """Collect diagnostic information from a failed client/process.

        Delegates to domain service for actual collection logic.
        """
        return collect_startup_diagnostics(client)

    def _finalize_start(self, client: Any, start_time: float) -> None:
        """Finalize successful mcp_server start."""
        self._client = client
        self._meta = {
            "init_result": {},
            "tools_count": self._tools.count(),
            "started_at": time.time(),
        }
        self._transition_to(McpServerState.READY)
        self._health.record_success()
        self._last_used = time.time()

        startup_duration_ms = (time.time() - start_time) * 1000
        self._record_event(
            McpServerStarted(
                mcp_server_id=self.mcp_server_id,
                mode=self._mode.value,
                tools_count=self._tools.count(),
                startup_duration_ms=startup_duration_ms,
            )
        )

        logger.info(f"mcp_server_started: {self.mcp_server_id}, mode={self._mode.value}, tools={self._tools.count()}")

        # Runtime capability drift check -- after READY, before lock release
        self._verify_capability_drift()

    def _verify_capability_drift(self) -> None:
        """Check runtime tools against declared expected_tools.

        Only flags undeclared runtime tools (tools present at runtime but NOT
        in expected_tools). Missing expected tools are not violations per
        CONTEXT.md decisions.

        Called inside _finalize_start() after READY transition, under lock.
        Records events via _record_event() (no I/O -- just appends to list).
        In block mode, transitions to DEAD immediately.
        """
        if self._capabilities is None:
            return
        expected = set(self._capabilities.tools.expected_tools)
        if not expected:
            return  # No expected_tools declared -- skip check

        actual = set(self._tools.list_names())
        undeclared = actual - expected

        if not undeclared:
            return

        violation_detail = f"Undeclared runtime tools: {sorted(undeclared)}"
        enforcement = self._capabilities.enforcement_mode

        self._record_event(
            CapabilityViolationDetected(
                mcp_server_id=self.mcp_server_id,
                violation_type=ViolationType.SCHEMA_MISMATCH.value,
                violation_detail=violation_detail,
                enforcement_action=enforcement,
                severity=ViolationSeverity.HIGH.value,
            )
        )

        logger.warning(
            "capability_drift_detected",
            mcp_server_id=self.mcp_server_id,
            undeclared_tools=sorted(undeclared),
            enforcement_mode=enforcement,
        )

        if enforcement == "block":
            self._transition_to(McpServerState.DEAD)

    def _handle_start_failure(self, error: Exception | None) -> None:
        """Handle start failure (must hold lock)."""
        # Clean up client if partially started
        if self._client:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 -- fault-barrier: cleanup must not mask original startup error
                pass
            self._client = None
            self._metrics_publisher.set_connection_active(self.mcp_server_id, False)

        self._health.record_failure()

        error_str = str(error) if error else "unknown error"

        # Determine new state
        if self._health.should_degrade():
            # Use direct assignment to avoid transition validation issues
            self._state = McpServerState.DEGRADED
            self._increment_version()

            logger.warning(f"mcp_server_degraded: {self.mcp_server_id}, failures={self._health.consecutive_failures}")

            self._record_event(
                McpServerDegraded(
                    mcp_server_id=self.mcp_server_id,
                    consecutive_failures=self._health.consecutive_failures,
                    total_failures=self._health.total_failures,
                    reason=error_str,
                )
            )
        else:
            self._state = McpServerState.DEAD
            self._increment_version()

        logger.error(f"mcp_server_start_failed: {self.mcp_server_id}, error={error_str}")

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:  # noqa: C901 -- baseline CC=22; split before extending
        """
        Invoke a tool on this mcp_server.

        Thread-safe. Ensures mcp_server is ready before invocation.

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
            CannotStartMcpServerError: If mcp_server cannot be started
            ToolNotFoundError: If tool doesn't exist
            ToolInvocationError: If invocation fails
        """
        from mcp_hangar.context import get_identity_context

        correlation_id = str(CorrelationId())
        idt_ctx = get_identity_context()
        identity_context_dict = idt_ctx.to_dict() if idt_ctx else None

        # L7 egress policy (MCPEgressPolicy): evaluate the tool call before we
        # wake the server or touch the upstream. The verdict (deny / require-
        # approval) is applied per the policy's mode (ADR-013):
        #   - Enforce (default): block -- raise, so a denied or approval-gated
        #     call never reaches the wire (and never cold-starts a server).
        #   - Audit: observe -- record the would-be verdict as a domain event and
        #     let the call proceed. This is the safe adoption path: an operator
        #     sees a policy's impact before switching it on.
        # A policy with no mode (programmatic or a mode-less older-operator
        # payload) resolves to Enforce -- it keeps blocking (fail-closed).
        if self._l7_policy is not None:
            from ..policies.egress_l7 import PolicyMode, ToolAction, evaluate

            decision = evaluate(tool_name, arguments, self._l7_policy)
            would_block = decision.action in (ToolAction.DENY, ToolAction.REQUIRE_APPROVAL)
            if would_block:
                if self._l7_policy.mode is PolicyMode.AUDIT:
                    self._record_event(
                        EgressPolicyViolationObserved(
                            mcp_server_id=self.mcp_server_id,
                            tool_name=tool_name,
                            would_be_action=decision.action.value,
                            reasons=list(decision.reasons),
                            correlation_id=correlation_id,
                            identity_context=identity_context_dict,
                        )
                    )
                    logger.warning(
                        "egress_policy_violation_observed",
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        would_be_action=decision.action.value,
                        reasons=list(decision.reasons),
                    )
                    # Audit mode: fall through and proceed with the call.
                elif decision.action is ToolAction.DENY:
                    raise EgressPolicyDeniedError(self.mcp_server_id, tool_name, "; ".join(decision.reasons))
                else:  # ToolAction.REQUIRE_APPROVAL
                    raise EgressPolicyApprovalRequiredError(self.mcp_server_id, tool_name)

        # Wait outside the invocation lock so a concurrent starter can finalize
        # state and signal every cold-start waiter.
        self.ensure_ready()

        # Lock cycle 1: Validation, check tool, maybe prepare refresh
        needs_refresh = False
        tool_found = False
        client = None
        with self._lock:
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
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        arguments=arguments,
                        identity_context=identity_context_dict,
                    )
                )

        # Refresh phase (outside lock): tools/list RPC
        if needs_refresh:
            refresh_error = None
            refresh_result = None
            # Copy client reference for I/O -- already validated as READY above
            with self._lock:
                refresh_client = self._client

            if refresh_client is None:
                raise ToolInvocationError(self.mcp_server_id, "mcp_server client is None")

            try:
                refresh_result = refresh_client.call("tools/list", {}, timeout=5.0)
            except (OSError, TimeoutError) as e:
                refresh_error = e
                logger.warning(f"tool_refresh_failed: {self.mcp_server_id}, error={e}")

            # Lock cycle 2: Apply refresh results, clear flag, re-check tool
            with self._lock:
                self._refresh_in_progress = False

                if refresh_error is None and refresh_result and "result" in refresh_result:
                    tool_list = refresh_result.get("result", {}).get("tools", [])
                    self._tools.update_from_list(tool_list)

                if not self._tools.has(tool_name):
                    raise ToolNotFoundError(self.mcp_server_id, tool_name)

                tool_found = True
                self._health._total_invocations += 1
                client = self._client
                self._record_event(
                    ToolInvocationRequested(
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        arguments=arguments,
                        identity_context=identity_context_dict,
                    )
                )
        elif not tool_found:
            # Another thread is refreshing but tool still not found -- raise
            raise ToolNotFoundError(self.mcp_server_id, tool_name)

        # Invocation phase (outside lock): tools/call RPC
        start_time = time.time()
        response = None
        invocation_error = None

        if client is None:
            raise ToolInvocationError(self.mcp_server_id, "mcp_server client is None")

        try:
            response = self._call_with_session_recovery(
                client,
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

                duration_ms = (time.time() - start_time) * 1000
                self._record_event(
                    ToolInvocationFailed(
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        duration_ms=duration_ms,
                        error_message=str(invocation_error),
                        error_type=type(invocation_error).__name__,
                        identity_context=identity_context_dict,
                    )
                )

                logger.error(
                    f"tool_invocation_failed: {correlation_id}, "
                    f"mcp_server={self.mcp_server_id}, tool={tool_name}, error={invocation_error}"
                )

                raise ToolInvocationError(
                    self.mcp_server_id,
                    str(invocation_error),
                    {"tool_name": tool_name, "correlation_id": correlation_id},
                ) from invocation_error

            if response is None:
                raise ToolInvocationError(self.mcp_server_id, "No response from mcp_server")

            if "error" in response:
                error_msg = response["error"].get("message", "unknown")
                self._health.record_invocation_failure()

                duration_ms = (time.time() - start_time) * 1000
                self._record_event(
                    ToolInvocationFailed(
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        duration_ms=duration_ms,
                        error_message=error_msg,
                        error_type=str(response["error"].get("code", "unknown")),
                        identity_context=identity_context_dict,
                    )
                )

                raise ToolInvocationError(
                    self.mcp_server_id,
                    f"tool_error: {error_msg}",
                    {"tool_name": tool_name, "correlation_id": correlation_id},
                )

            result = response.get("result", {})

            # A backend tool result with isError:true is a tool-level failure,
            # even though the JSON-RPC envelope carries no protocol error. Map it
            # to a failure here (the single authoritative point) so downstream
            # health, batch counts, and events all reflect reality. The front
            # door still re-surfaces the raw result verbatim to the caller.
            if isinstance(result, dict) and result.get("isError"):
                # Tool-level errors live in the content blocks (typically
                # [{"type": "text", "text": ...}]), not the JSON-RPC error
                # envelope. Extract the text for diagnostics.
                content = result.get("content")
                error_msg = "tool reported isError"
                if isinstance(content, list):
                    texts = [str(block["text"]) for block in content if isinstance(block, dict) and block.get("text")]
                    if texts:
                        error_msg = "; ".join(texts)

                self._health.record_invocation_failure()

                duration_ms = (time.time() - start_time) * 1000
                self._record_event(
                    ToolInvocationFailed(
                        mcp_server_id=self.mcp_server_id,
                        tool_name=tool_name,
                        correlation_id=correlation_id,
                        duration_ms=duration_ms,
                        error_message=error_msg,
                        error_type="tool_error",
                        identity_context=identity_context_dict,
                    )
                )

                raise ToolInvocationError(
                    self.mcp_server_id,
                    f"tool_error: {error_msg}",
                    {
                        "tool_name": tool_name,
                        "correlation_id": correlation_id,
                        "is_error": True,
                        "content": result.get("content"),
                    },
                )

            # Success
            duration_ms = (time.time() - start_time) * 1000
            self._health.record_success()
            self._last_used = time.time()

            self._record_event(
                ToolInvocationCompleted(
                    mcp_server_id=self.mcp_server_id,
                    tool_name=tool_name,
                    correlation_id=correlation_id,
                    duration_ms=duration_ms,
                    result_size_bytes=len(str(result)),
                    identity_context=identity_context_dict,
                )
            )

            logger.debug(f"tool_invoked: {correlation_id}, mcp_server={self.mcp_server_id}, tool={tool_name}")

            return cast(dict[str, Any], result)

    def relay_request(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Relay a raw JSON-RPC request to the live upstream client.

        This is the follow-up path for an already-minted governed task (ADR-014
        task relay): a client later sends ``tasks/get`` / ``tasks/result`` /
        ``tasks/cancel`` and Hangar must forward that request verbatim to the
        SAME upstream MCP server that minted the task.

        Unlike ``invoke_tool``, this NEVER cold-starts the server. If the server
        is not already live we fail -- we do not launch it (the task's upstream
        is gone, so there is nothing to relay to). It applies no L7/egress/consent
        logic; it is a thin transport relay. Trace/_meta injection is handled
        inside ``client.call``.

        The client reference is copied under the same lock ``invoke_tool`` uses
        (see the invocation-phase pattern around mcp_server.py:1095), and the
        network ``.call`` is issued OUTSIDE the lock.

        Args:
            method: JSON-RPC method to forward verbatim (e.g. "tasks/get").
            params: JSON-RPC params to forward verbatim.
            timeout: Timeout in seconds.

        Returns:
            The raw JSON-RPC response dict as-is (the ``{"result": ...}`` /
            ``{"error": ...}`` shape). Not unwrapped or interpreted -- the caller
            validates the payload into a typed result.

        Raises:
            ToolInvocationError: If the upstream client is absent or not alive
                (relay-unavailable), or if the transport call fails.
        """
        # Copy the live client under the lock; do NOT cold-start. Mirrors the
        # invoke_tool invocation-phase copy-under-lock-then-call-outside-lock.
        with self._lock:
            client = self._client
            if client is None or not client.is_alive():
                raise ToolInvocationError(
                    self.mcp_server_id,
                    "relay unavailable: mcp_server client is not live",
                    {"method": method},
                )

        # Transport phase (outside lock): forward method + params verbatim.
        try:
            response = client.call(method, params, timeout=timeout)
        except (OSError, TimeoutError) as e:
            raise ToolInvocationError(
                self.mcp_server_id,
                str(e),
                {"method": method},
            ) from e

        # Return the raw response dict as-is; the caller validates the payload.
        # No cast needed since the client is typed by TransportClient -- it was
        # only ever there because the attribute was Any.
        return response

    def _refresh_tools(self) -> None:
        """Refresh tool catalog from mcp_server.

        Note: This performs I/O (tools/list RPC). Callers should prefer the
        two-lock-cycle pattern in invoke_tool() which performs the RPC outside
        the lock. This method is retained for internal use but should NOT be
        called while holding the mcp_server lock.
        """
        if not self._client or not self._client.is_alive():
            return

        try:
            tools_resp = self._client.call("tools/list", {}, timeout=5.0)
            if "result" in tools_resp:
                tool_list = tools_resp.get("result", {}).get("tools", [])
                self._tools.update_from_list(tool_list)
        except (OSError, TimeoutError) as e:
            logger.warning(f"tool_refresh_failed: {self.mcp_server_id}, error={e}")

    def health_check(self) -> bool:
        """
        Perform active health check.

        Thread-safe. Returns True if healthy.

        Note: Follows "copy reference under lock, I/O outside lock" pattern.
        """
        # Phase 1: Check state and get client reference under lock
        with self._lock:
            if self._state != McpServerState.READY:
                return False

            if not self._client or not self._client.is_alive():
                self._state = McpServerState.DEAD
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
            if self._state != McpServerState.READY:
                return False

            if check_error is not None:
                self._health.record_failure()

                self._record_event(
                    HealthCheckFailed(
                        mcp_server_id=self.mcp_server_id,
                        consecutive_failures=self._health.consecutive_failures,
                        error_message=str(check_error),
                    )
                )

                logger.warning(f"health_check_failed: {self.mcp_server_id}, error={check_error}")

                if self._health.should_degrade():
                    self._state = McpServerState.DEGRADED
                    self._increment_version()

                    logger.warning(f"mcp_server_degraded_by_health_check: {self.mcp_server_id}")

                    self._record_event(
                        McpServerDegraded(
                            mcp_server_id=self.mcp_server_id,
                            consecutive_failures=self._health.consecutive_failures,
                            total_failures=self._health.total_failures,
                            reason="health_check_failures",
                        )
                    )

                return False

            # Success
            duration_ms = (time.time() - start_time) * 1000
            self._health.record_success()

            self._record_event(HealthCheckPassed(mcp_server_id=self.mcp_server_id, duration_ms=duration_ms))

            return True

    def maybe_shutdown_idle(self) -> bool:
        """
        Shutdown if idle past TTL.

        Thread-safe. Returns True if shutdown was performed.
        """
        with self._lock:
            if self._state != McpServerState.READY:
                return False

            idle_time = time.time() - self._last_used
            if idle_time > self._idle_ttl.seconds:
                self._record_event(
                    McpServerIdleDetected(
                        mcp_server_id=self.mcp_server_id,
                        idle_duration_s=idle_time,
                        last_used_at=self._last_used,
                    )
                )

                logger.info(f"mcp_server_idle_shutdown: {self.mcp_server_id}, idle={idle_time:.1f}s")
                self._shutdown_internal(reason="idle")
                return True

            return False

    def shutdown(self) -> None:
        """Explicit shutdown (public API). Thread-safe."""
        with self._lock:
            self._shutdown_internal(reason="shutdown")

    def stop(self) -> None:
        """Stop the mcp_server. Alias for shutdown(). Thread-safe."""
        self.shutdown()

    def _shutdown_internal(self, reason: str = "shutdown") -> None:
        """Shutdown implementation (must hold lock)."""
        if self._client:
            try:
                self._client.close()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: shutdown cleanup must not propagate
                logger.warning(f"shutdown_error: {self.mcp_server_id}, error={e}")
            self._client = None
            self._metrics_publisher.set_connection_active(self.mcp_server_id, False)

        self._state = McpServerState.COLD
        self._increment_version()
        self._tools.clear()
        self._meta.clear()

        self._record_event(McpServerStopped(mcp_server_id=self.mcp_server_id, reason=reason))

    # --- Compatibility Methods ---

    def get_tool_names(self) -> list[str]:
        """Get list of available tool names."""
        with self._lock:
            return self._tools.list_names()

    def get_tools_dict(self) -> dict[str, ToolSchema]:
        """Get tools as dictionary (for backward compatibility)."""
        with self._lock:
            return self._tools.to_dict()

    def get_tool_schemas(self) -> list[ToolSchema]:
        """Return a copy of the current tool schemas.

        Returns the tools known to this mcp_server. Thread-safe: takes a
        snapshot under lock and returns it.

        Returns:
            List of ToolSchema value objects.
        """
        with self._lock:
            return list(self._tools.list_tools())

    def to_status_dict(self) -> dict[str, Any]:
        """Get status as dictionary (for registry.list)."""
        with self._lock:
            return {
                "mcp_server": self.mcp_server_id,
                "state": self._state.value,
                "alive": self._client is not None and self._client.is_alive(),
                "mode": self._mode.value,
                "image_or_command": self._image or self._command,
                "tools_cached": self._tools.list_names(),
                "health": self._health.to_dict(),
                "meta": dict(self._meta),
            }

    _SECRET_KEYS = frozenset({"bearer_token", "api_key", "basic_password"})

    @staticmethod
    def _redact_auth_config(auth: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of auth config with secret values redacted.

        Args:
            auth: Authentication configuration dict.

        Returns:
            Copy with sensitive values replaced by [REDACTED].
        """
        redacted = {}
        for k, v in auth.items():
            if k in McpServer._SECRET_KEYS and isinstance(v, str):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = v
        return redacted

    def to_config_dict(self) -> dict[str, Any]:
        """Return YAML-compatible config spec dict.

        Returns the minimal representation for round-trip:
        load_config(to_config_dict()) produces an equivalent McpServer.
        Note: auth secrets are redacted (bearer_token, api_key, basic_password
        replaced with [REDACTED]) since output is used in API responses and logs.
        This means the output is NOT suitable for lossless round-trip of secrets.

        Returns:
            Dictionary of mcp_server configuration fields, omitting optional
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
        if self._mode == McpServerMode.REMOTE:
            if self._auth_config:
                spec["auth"] = self._redact_auth_config(self._auth_config)
            if self._tls_config:
                spec["tls"] = dict(self._tls_config)
            if self._http_config:
                spec["http"] = dict(self._http_config)
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
        self._record_event(McpServerUpdated(mcp_server_id=self.mcp_server_id, source="api"))

    # ------------------------------------------------------------------
    # Replay -- rebuilding lifecycle state from this aggregate's stream
    # ------------------------------------------------------------------

    def restore_from_events(self, events: "list[DomainEvent]") -> int:
        """Rebuild lifecycle state by replaying this server's own stream.

        What replay restores and what it deliberately does not is the whole
        design, so it is written down rather than implied:

        * **From the stream:** state, health counters, invocation totals, last
          use. A server that was DEGRADED before a restart comes back DEGRADED.
          Discarding that would hand every process restart a free breaker reset,
          which is the one thing an enforcement plane must not do quietly.
        * **From configuration, not the stream:** mode, command, image,
          endpoint, env, TTLs, thresholds. Those are what the operator asked
          for, and the answer to "what should this be" is not in history.
        * **Never restored:** the live transport client and any process handle.
          Liveness is re-earned by connecting, never assumed from a record.

        Events are applied in stream order; the last one wins. Unknown event
        types are skipped rather than raising -- a stream written by a newer
        version must not stop an older one from booting.

        Args:
            events: This aggregate's events, oldest first.

        Returns:
            How many events changed state.
        """
        applied = 0
        with self._lock:
            for event in events:
                handler = self._replay_handler_for(type(event))
                if handler is None:
                    continue
                handler(self, event)
                applied += 1
        if applied:
            logger.info(
                "lifecycle_state_restored",
                mcp_server_id=self.mcp_server_id,
                events_applied=applied,
                state=str(self._state),
            )
        return applied

    @classmethod
    def _replay_handler_for(cls, event_class: type) -> "Any | None":
        """The replay handler for an event class or the closest base it has.

        Walks the MRO for the same reason bus dispatch does: the deprecated
        `Provider*` aliases subclass their `McpServer*` counterparts, and a
        stream written before the rename replays through exactly this path.
        """
        for klass in event_class.__mro__:
            handler = _REPLAY_HANDLERS.get(klass)
            if handler is not None:
                return handler
        return None

    def _replay_started(self, event: "McpServerStarted") -> None:
        self._state = McpServerState.READY
        self._health.restore(consecutive_failures=0)
        self._last_used = event.occurred_at

    def _replay_stopped(self, event: "McpServerStopped") -> None:
        self._state = McpServerState.COLD
        # The handle died with the process that held it; a restored aggregate
        # must reconnect rather than believe a record about liveness.
        self._client = None
        self._tools.clear()

    def _replay_degraded(self, event: "McpServerDegraded") -> None:
        self._state = McpServerState.DEGRADED
        self._health.restore(
            consecutive_failures=event.consecutive_failures,
            total_failures=event.total_failures,
        )

    def _replay_state_changed(self, event: "McpServerStateChanged") -> None:
        self._state = McpServerState(event.new_state)

    def _replay_tool_completed(self, event: "ToolInvocationCompleted") -> None:
        self._health.restore(consecutive_failures=0, last_success_at=event.occurred_at)
        self._last_used = event.occurred_at

    def _replay_tool_failed(self, event: "ToolInvocationFailed") -> None:
        self._health.restore(
            consecutive_failures=self._health.consecutive_failures + 1,
            total_failures=self._health.total_failures + 1,
            last_failure_at=event.occurred_at,
        )

    def _replay_health_passed(self, event: "HealthCheckPassed") -> None:
        self._health.restore(consecutive_failures=0, last_success_at=event.occurred_at)

    def _replay_health_failed(self, event: "HealthCheckFailed") -> None:
        self._health.restore(
            consecutive_failures=event.consecutive_failures,
            last_failure_at=event.occurred_at,
        )


# Replay handlers, as a table rather than an isinstance chain: the chain form
# is what the complexity gate caps, and a dict is what lets `_replay_handler_for`
# walk the MRO so a legacy `Provider*` event finds its modern handler.
_REPLAY_HANDLERS: "dict[type, Any]" = {
    McpServerStarted: McpServer._replay_started,
    McpServerStopped: McpServer._replay_stopped,
    McpServerDegraded: McpServer._replay_degraded,
    McpServerStateChanged: McpServer._replay_state_changed,
    ToolInvocationCompleted: McpServer._replay_tool_completed,
    ToolInvocationFailed: McpServer._replay_tool_failed,
    HealthCheckPassed: McpServer._replay_health_passed,
    HealthCheckFailed: McpServer._replay_health_failed,
}

# legacy aliases
Provider = McpServer

ProviderState = McpServerState
