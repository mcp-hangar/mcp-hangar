"""Cloud connector -- orchestrates registration, heartbeat, event forwarding, and state sync.

The connector is the single entry point used by bootstrap. It subscribes to
the event bus, manages async workers in a dedicated thread, and gracefully
shuts down on stop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, TYPE_CHECKING

import httpx

from .buffer import EventBuffer
from .client import CloudClient
from .config import CloudConfig
from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..domain.events import DomainEvent

logger = get_logger(__name__)

# Domain event types that should be forwarded to the cloud.
_FORWARDED_EVENT_TYPES: set[str] = {
    "DetectionRuleMatched",
    "EnforcementActionTaken",
    "ToolInvocationRequested",
    "ToolInvocationCompleted",
    "ToolInvocationFailed",
    "ProviderStarted",
    "ProviderStopped",
    "ProviderDegraded",
    "ProviderStateChanged",
    "HealthCheckPassed",
    "HealthCheckFailed",
    "CircuitBreakerStateChanged",
    "ProviderDiscovered",
    "ProviderDiscoveryLost",
    "ProviderQuarantined",
    "ProviderApproved",
    "CapabilityViolationDetected",
    "EgressBlocked",
    "ToolSchemaDriftDetected",
    "ToolApprovalRequested",
    "ToolApprovalGranted",
    "ToolApprovalDenied",
    "PolicyViolation",
}

_RETRY_DELAYS = [1, 2, 4, 8, 16, 32, 60]  # exponential backoff ceiling at 60s

# Keys stripped from event payloads before forwarding to cloud.
# Tool arguments may contain secrets, PII, or credentials supplied by users.
# Error messages may leak internal paths or sensitive context.
_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "arguments",
        "error_message",
        "identity_context",
    }
)


class CloudConnector:
    """Lightweight cloud connector for standalone mcp-hangar instances.

    Runs a dedicated asyncio event loop in a daemon thread so it works
    regardless of whether the host server uses stdio or HTTP mode.

    Lifecycle:
      1. ``start()`` -- spawns thread, registers with cloud, starts async workers
      2. event bus handler pushes events into a bounded buffer
      3. workers periodically flush events, send heartbeats, sync state
      4. ``stop()``  -- flushes remaining events, deregisters, closes HTTP
    """

    def __init__(self, config: CloudConfig, providers: dict[str, Any]) -> None:
        self._cfg = config
        self._providers = providers  # reference to PROVIDERS dict
        self._buffer = EventBuffer(max_size=config.buffer_max_size)
        self._started_at: float = time.monotonic()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._connected = False
        self._dormant = False

    # -- event handler (called synchronously from event bus thread) ---------

    def on_event(self, event: DomainEvent) -> None:
        """Synchronous handler wired to ``event_bus.subscribe_to_all``."""
        etype = type(event).__name__
        if etype not in _FORWARDED_EVENT_TYPES:
            return
        try:
            payload = event.to_dict()
        except (AttributeError, TypeError, ValueError):
            payload = {"event_type": etype}
        self._buffer.push(_redact_event_payload(payload))

    # -- thread lifecycle ---------------------------------------------------

    def start(self) -> None:
        """Spawn the cloud connector thread."""
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="cloud-connector")
        self._thread.start()
        logger.info("cloud_connector_thread_started")

    def stop(self) -> None:
        """Signal stop and wait for graceful shutdown (max 10s)."""
        if self._loop is None:
            return
        # Schedule the async shutdown on the connector's event loop
        future = asyncio.run_coroutine_threadsafe(self._async_stop(), self._loop)
        try:
            future.result(timeout=10.0)
        except (TimeoutError, RuntimeError, OSError):
            pass
        # Stop the event loop
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5.0)
        logger.info("cloud_connector_stopped")

    # -- internal async logic -----------------------------------------------

    def _run_loop(self) -> None:
        """Entry point for the dedicated thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._async_main())
        except (RuntimeError, OSError) as exc:
            logger.error("cloud_connector_loop_error", error=str(exc))
        finally:
            self._loop.close()

    async def _async_main(self) -> None:
        """Main async coroutine: register then run workers forever."""
        client = CloudClient(self._cfg)
        self._client = client
        self._stop_event = asyncio.Event()

        registered = await self._register_with_retry(client)
        if not registered:
            self._dormant = True
            logger.error(
                "cloud_connector_dormant",
                reason="registration_failed",
                max_attempts=self._cfg.max_registration_attempts,
                probe_interval_s=self._cfg.dormant_probe_interval_s,
            )
            await self._dormant_probe_loop(client)
            return

        tasks = [
            asyncio.create_task(self._heartbeat_loop(client), name="cloud-hb"),
            asyncio.create_task(self._event_flush_loop(client), name="cloud-ev"),
            asyncio.create_task(self._state_sync_loop(client), name="cloud-ss"),
        ]

        # Run until stop_event is set
        await self._stop_event.wait()

        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _async_stop(self) -> None:
        """Flush remaining events and deregister."""
        # Best-effort final flush
        try:
            batch = self._buffer.drain(500)
            if batch and hasattr(self, "_client"):
                await self._client.send_events(batch)
        except (httpx.HTTPError, OSError):
            pass
        if hasattr(self, "_client"):
            await self._client.deregister()
            await self._client.close()
        if hasattr(self, "_stop_event"):
            self._stop_event.set()

    # -- registration -------------------------------------------------------

    async def _register_with_retry(self, client: CloudClient) -> bool:
        """Attempt registration up to max_registration_attempts times.

        Returns True on success, False when all attempts are exhausted.
        """
        max_attempts = self._cfg.max_registration_attempts
        for attempt, delay in enumerate(_retry_with_backoff()):
            if attempt >= max_attempts:
                return False
            if self._stop_event.is_set():
                return False
            try:
                await client.register()
                self._connected = True
                return True
            except (httpx.HTTPError, OSError) as exc:
                self._connected = False
                logger.warning(
                    "cloud_register_failed",
                    attempt=attempt + 1,
                    remaining=max_attempts - attempt - 1,
                    error=str(exc),
                    retry_in=delay,
                )
                await self._interruptible_sleep(delay)
        return False

    # -- dormant probe (cloud unreachable) ----------------------------------

    async def _dormant_probe_loop(self, client: CloudClient) -> None:
        """Periodically retry registration after entering dormant mode.

        On success, exits dormant mode and starts normal worker loops.
        """
        while not self._stop_event.is_set():
            await self._interruptible_sleep(self._cfg.dormant_probe_interval_s)
            if self._stop_event.is_set():
                return
            try:
                await client.register()
                self._connected = True
                self._dormant = False
                logger.info("cloud_connector_recovered")
                tasks = [
                    asyncio.create_task(self._heartbeat_loop(client), name="cloud-hb"),
                    asyncio.create_task(self._event_flush_loop(client), name="cloud-ev"),
                    asyncio.create_task(self._state_sync_loop(client), name="cloud-ss"),
                ]
                await self._stop_event.wait()
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return
            except (httpx.HTTPError, OSError):
                pass

    # -- heartbeat ----------------------------------------------------------

    async def _heartbeat_loop(self, client: CloudClient) -> None:
        while not self._stop_event.is_set():
            try:
                pcount, hcount = self._provider_counts()
                uptime = time.monotonic() - self._started_at
                await client.heartbeat(pcount, hcount, uptime)
                self._connected = True
            except (httpx.HTTPError, OSError) as exc:
                self._connected = False
                logger.debug("cloud_heartbeat_failed", error=str(exc))
            await self._interruptible_sleep(self._cfg.heartbeat_interval_s)

    # -- event flush --------------------------------------------------------

    async def _event_flush_loop(self, client: CloudClient) -> None:
        while not self._stop_event.is_set():
            await self._interruptible_sleep(self._cfg.batch_interval_s)
            await self._flush_events(client)

    async def _flush_events(self, client: CloudClient) -> None:
        batch = self._buffer.drain(500)
        if not batch:
            return
        try:
            await client.send_events(batch)
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("cloud_event_flush_failed", error=str(exc), batch_size=len(batch))
            # Push back into buffer (best-effort, may lose some if buffer is full)
            for ev in batch:
                self._buffer.push(ev)

    # -- state sync ---------------------------------------------------------

    async def _state_sync_loop(self, client: CloudClient) -> None:
        # Initial sync right after registration
        await self._sync_state(client)
        while not self._stop_event.is_set():
            await self._interruptible_sleep(self._cfg.state_sync_interval_s)
            await self._sync_state(client)

    async def _sync_state(self, client: CloudClient) -> None:
        try:
            snapshots = self._build_provider_snapshots()
            await client.sync_state(snapshots)
        except (httpx.HTTPError, OSError) as exc:
            logger.debug("cloud_state_sync_failed", error=str(exc))

    # -- helpers ------------------------------------------------------------

    def _provider_counts(self) -> tuple[int, int]:
        total = len(self._providers)
        healthy = 0
        for p in self._providers.values():
            try:
                if hasattr(p, "state") and str(p.state) == "ready":
                    healthy += 1
            except (AttributeError, TypeError):
                pass
        return total, healthy

    def _build_provider_snapshots(self) -> list[dict[str, Any]]:
        snapshots: list[dict[str, Any]] = []
        for pid, p in self._providers.items():
            snap: dict[str, Any] = {"id": pid}
            try:
                snap["status"] = str(p.state).upper() if hasattr(p, "state") else "UNKNOWN"
            except (AttributeError, TypeError):
                snap["status"] = "UNKNOWN"
            try:
                snap["mode"] = str(p.mode) if hasattr(p, "mode") else "unknown"
            except (AttributeError, TypeError):
                snap["mode"] = "unknown"
            try:
                if hasattr(p, "tools"):
                    tools = p.tools
                    snap["tools"] = [t.name if hasattr(t, "name") else str(t) for t in tools]
                elif hasattr(p, "tool_names"):
                    snap["tools"] = list(p.tool_names)
                else:
                    snap["tools"] = []
            except (AttributeError, TypeError):
                snap["tools"] = []
            try:
                snap["health"] = "healthy" if str(p.state) == "ready" else "unhealthy"
            except (AttributeError, TypeError):
                snap["health"] = "unknown"
            snapshots.append(snap)
        return snapshots

    async def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep that wakes up early when stop is requested."""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except TimeoutError:
            pass

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def dormant(self) -> bool:
        return self._dormant


def _redact_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip sensitive fields from an event payload before cloud forwarding."""
    return {k: v for k, v in payload.items() if k not in _REDACTED_KEYS}


def _retry_with_backoff():
    """Infinite generator yielding delay seconds with exponential backoff."""
    idx = 0
    while True:
        yield _RETRY_DELAYS[min(idx, len(_RETRY_DELAYS) - 1)]
        idx += 1
