"""Discovery Orchestrator.

Main coordination component for mcp_server discovery.
Manages discovery sources, validation, and integration with the registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from mcp_hangar.application.services.log_pacing import RepeatedFailure
from mcp_hangar.domain.contracts.event_bus import IEventBus
from mcp_hangar.domain.discovery.conflict_resolver import ConflictResolver
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_service import DiscoveryCycleResult, DiscoveryService
from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
from mcp_hangar.domain.events import (
    McpServerDiscovered,
    McpServerDiscoveryConfigChanged,
    McpServerDiscoveryLost,
    McpServerQuarantined,
)
from mcp_hangar.domain.exceptions import McpServerNotHereError
from mcp_hangar.logging_config import get_logger
from mcp_hangar.observability.conventions import Discovery
from mcp_hangar.observability.tracing import get_tracer, record_handled_failure

if TYPE_CHECKING:
    from mcp_hangar.domain.security.input_validator import InputValidator

# Import main metrics for unified observability
from mcp_hangar import metrics as main_metrics

from .lifecycle_manager import DiscoveryLifecycleManager
from .security_validator import SecurityConfig, SecurityValidator

logger = get_logger(__name__)


@dataclass
class DiscoveryConfig:
    """Configuration for discovery orchestrator.

    Attributes:
        enabled: Master switch for discovery
        refresh_interval_s: Interval between discovery cycles
        auto_register: Whether to auto-register discovered mcp_servers
        security: Security configuration
        lifecycle: Lifecycle configuration
    """

    enabled: bool = True
    refresh_interval_s: int = 30
    auto_register: bool = True

    # Security settings
    security: SecurityConfig = field(default_factory=SecurityConfig)

    # Lifecycle settings
    default_ttl_s: int = 90
    check_interval_s: int = 10
    drain_timeout_s: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryConfig:
        """Create from dictionary (e.g., from config.yaml).

        Args:
            data: Configuration dictionary

        Returns:
            DiscoveryConfig instance
        """
        security_data = data.get("security", {})
        lifecycle_data = data.get("lifecycle", {})

        return cls(
            enabled=data.get("enabled", True),
            refresh_interval_s=data.get("refresh_interval_s", 30),
            auto_register=data.get("auto_register", True),
            security=SecurityConfig.from_dict(security_data),
            default_ttl_s=lifecycle_data.get("default_ttl_s", 90),
            check_interval_s=lifecycle_data.get("check_interval_s", 10),
            drain_timeout_s=lifecycle_data.get("drain_timeout_s", 30),
        )


# Type for registry registration callback
RegistrationCallback = Callable[[DiscoveredMcpServer], Awaitable[bool]]
DeregistrationCallback = Callable[[str, str], Awaitable[None]]
#: Applies a changed definition of a registered server; raises when it is not applied.
UpdateCallback = Callable[[DiscoveredMcpServer], Awaitable[None]]


class DiscoveryChangeRefused(Exception):
    """The control plane refused a changed definition; the running one stays (#2091).

    Raised by an update callback for a refusal that asking again cannot change,
    so the same definition is not tried again. Any other exception is retried
    on the next cycle.
    """


class DiscoveryOrchestrator:
    """Main coordination component for mcp_server discovery.

    Orchestrates:
        - Multiple discovery sources
        - Security validation pipeline
        - Lifecycle management (TTL, quarantine)
        - Integration with main registry
        - Metrics and observability

    Usage:
        orchestrator = DiscoveryOrchestrator(config)
        orchestrator.add_source(KubernetesDiscoverySource())
        orchestrator.add_source(DockerDiscoverySource())

        # Set callbacks for registry integration
        orchestrator.on_register = async_register_fn
        orchestrator.on_deregister = async_deregister_fn

        # Start discovery
        await orchestrator.start()
    """

    def __init__(
        self,
        config: DiscoveryConfig | None = None,
        static_mcp_servers: set[str] | None = None,
        input_validator: InputValidator | None = None,
        event_bus: IEventBus | None = None,
        may_manage: Callable[[], bool] | None = None,
    ):
        """Initialize discovery orchestrator.

        Args:
            config: Discovery configuration
            static_mcp_servers: Set of static mcp_server names (from config)
            input_validator: Optional InputValidator for command validation
            event_bus: Where discovery's own events go. Optional because the
                orchestrator runs in tests without one; when it is absent the
                events are simply not emitted, and nothing else changes.
            may_manage: Whether this instance currently holds the management
                lease. Asked once per cycle rather than once at startup: a
                lease lost mid-life has to stop the next cycle, not the next
                process. Absent means yes, which is a standalone gateway and
                every existing deployment.
        """
        self.config = config or DiscoveryConfig()
        self._input_validator = input_validator
        self._event_bus = event_bus
        self._may_manage = may_manage or (lambda: True)
        #: Reports the first skipped cycle, then one in this many. At the
        #: default 30s refresh that is a line roughly every five minutes while
        #: this instance is not the holder -- present in the log, absent from
        #: the reader's attention.
        self._idle = RepeatedFailure(every=10)

        # Core components
        self._conflict_resolver = ConflictResolver(static_mcp_servers)
        self._discovery_service = DiscoveryService(
            conflict_resolver=self._conflict_resolver,
            auto_register=self.config.auto_register,
        )
        self._validator = SecurityValidator(self.config.security)
        self._lifecycle_manager = DiscoveryLifecycleManager(
            default_ttl=self.config.default_ttl_s,
            check_interval=self.config.check_interval_s,
            drain_timeout=self.config.drain_timeout_s,
            # TTL expiry *deregisters* servers. Of everything discovery does it
            # is the most destructive, and the one a follower running on a stale
            # view would get most wrong. Asked through the tenure observer, so
            # that whichever loop sees a new tenure first rebuilds tracking
            # before this loop's expiry check runs (#2111).
            may_manage=lambda: self._manages(),
        )

        # Callbacks for registry integration
        self.on_register: RegistrationCallback | None = None
        self.on_deregister: DeregistrationCallback | None = None
        #: Applies a changed definition of a server already registered (#2091).
        #: Unset, a change goes to `on_register`, as it always did.
        self.on_update: UpdateCallback | None = None
        # Changed definitions not applied yet, all by server name: the
        # fingerprint whose change was recorded as an event, the one the
        # control plane refused for good, and the (fingerprint, reason) last
        # logged. Each says something once rather than every cycle.
        self._change_announced: dict[str, str] = {}
        self._change_refused: dict[str, str] = {}
        self._change_logged: dict[str, tuple[str, str]] = {}
        #: The discovered servers already in the fleet, taken back at the start
        #: of each management tenure (#2106). None takes back nothing.
        self.restored_servers: Callable[[], list[DiscoveredMcpServer]] | None = None
        self._took_back_restored = False
        #: The lease as last observed: None before the first look (#2111).
        self._holding: bool | None = None

        # Discovery loop state
        self._running = False
        self._discovery_task: asyncio.Task[None] | None = None
        self._last_cycle: datetime | None = None
        #: The event loop discovery runs on, captured in start(). The
        #: orchestrator's background cycle runs on a dedicated long-lived loop in
        #: its own thread (ServerLifecycle._start_discovery). A synchronous
        #: caller -- a CQRS command handler dispatched via run_in_threadpool --
        #: schedules a manual scan back onto this loop so it serialises with the
        #: periodic cycle instead of racing shared DiscoveryService state on a
        #: second loop. None until started (tests, stdio without discovery).
        self._loop: asyncio.AbstractEventLoop | None = None

    def _emit(self, event: Any) -> None:
        """Record something discovery did, if there is a bus to record it on.

        Discovery is the one door into the fleet nobody opens by hand, which is
        why its history is worth keeping: a server appeared, its definition
        changed under us, it was refused, it went away. Five event classes for
        exactly this were declared long ago and never emitted by anything --
        the vocabulary existed, the feature was live, and the log stayed empty
        (#762).

        Failures here are swallowed on purpose. The bus already has a fault
        barrier around handlers and keeps delivering when the store cannot
        write; this is the last line of defence, so that recording an event can
        never be the reason a discovery cycle dies.
        """
        if self._event_bus is None:
            return
        try:
            self._event_bus.publish(event)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: bookkeeping must not stop discovery
            logger.warning(
                "discovery_event_not_recorded",
                event_type=type(event).__name__,
                error=str(e),
            )

    def add_source(self, source: DiscoverySource) -> None:
        """Add a discovery source.

        Args:
            source: Discovery source to add
        """
        self._discovery_service.register_source(source)
        logger.info(f"Added discovery source: {source.source_type}")

    def get_sources(self) -> list[DiscoverySource]:
        """The sources this orchestrator actually holds.

        A configured source reaches this list only if it was built: an optional
        dependency that is missing, or any other failure during construction,
        degrades to a log line and the source is simply absent. So anything that
        needs to speak about "the sources" has to ask here rather than re-read
        the configuration, which describes what was asked for rather than what
        exists.

        Synchronous on purpose -- `get_sources_status()` queries each source's
        health, which bootstrap cannot await.

        Returns:
            The built sources, in registration order.
        """
        return self._discovery_service.get_all_sources()

    def get_source(self, source_type: str) -> DiscoverySource | None:
        """The live running source of a given type, or None if not held.

        The registry keeps immutable specs; the running source is here. When a
        spec is toggled or reconfigured the registry reaches back through this to
        the live instance, because that instance is what the discovery cycle and
        get_sources_status() both read for enabled state -- so the spec and the
        thing actually being scanned stay in step.
        """
        return self._discovery_service.get_source(source_type)

    def remove_source(self, source_type: str) -> DiscoverySource | None:
        """Remove a discovery source.

        Args:
            source_type: Type of source to remove

        Returns:
            Removed source, or None if not found
        """
        return self._discovery_service.unregister_source(source_type)

    def add_source_live(self, source: DiscoverySource) -> None:
        """Add a source to a running orchestrator and start it, from a synchronous caller (#1674).

        A source added after `start()` is not started by it. When discovery runs
        on its own loop, the add and the start are scheduled onto that loop, so
        the source map is changed where the cycle reads it and not from the
        command bus's worker thread; before `start()` it is a plain add.
        """
        loop = self._loop
        if loop is not None and loop.is_running():

            async def _add_and_start() -> None:
                self.add_source(source)
                await source.start()

            asyncio.run_coroutine_threadsafe(_add_and_start(), loop).result(timeout=30)
            return
        self.add_source(source)

    def remove_source_live(self, source_type: str) -> DiscoverySource | None:
        """Remove a source from a running orchestrator and stop it, from a synchronous caller (#1674)."""
        loop = self._loop
        if loop is not None and loop.is_running():

            async def _remove_and_stop() -> DiscoverySource | None:
                removed = self.remove_source(source_type)
                if removed is not None:
                    await removed.stop()
                return removed

            return asyncio.run_coroutine_threadsafe(_remove_and_stop(), loop).result(timeout=30)
        return self.remove_source(source_type)

    def set_static_mcp_servers(self, names: set[str]) -> None:
        """Set static mcp_server names (from config).

        Args:
            names: Set of static mcp_server names
        """
        self._discovery_service.set_static_mcp_servers(names)

    def _take_back_restored(self) -> None:
        """Track the discovered servers already in the fleet, once per management tenure (#2106).

        Servers discovery registered before a restart, or that another replica
        registered while this one followed. Each is tracked as if its source had
        just reported it: the source's mode then governs it, an authoritative
        source that no longer reports it removes it one TTL later, and a changed
        definition reads as a change.

        At the start of a tenure, not at boot: the TTL runs from when this
        instance may act on it. Taken at boot, it would run out while this
        instance waited for the lease, and the first expiry check after taking
        it would delete every discovered server before a cycle could see them.

        A server whose changed definition waits in quarantine is taken back too:
        its row is the definition that runs, and the change is approved, applied
        or removed against it (#2111).
        """
        if self.restored_servers is None or self._took_back_restored or not self._manages():
            return
        self._took_back_restored = True
        taken = []
        for mcp_server in self.restored_servers():
            if self.is_tracking(mcp_server.name):
                continue
            self._conflict_resolver.register(mcp_server)
            self._lifecycle_manager.add_mcp_server(mcp_server)
            self._validator.record_registration(mcp_server)
            taken.append(mcp_server.name)
        if taken:
            logger.info("discovery_restored_servers_tracked", mcp_server_ids=sorted(taken))

    def _manages(self) -> bool:
        """Whether this instance holds the management lease, and the one place that asks (#2111).

        Every gate on discovery's loop goes through here -- the discovery loop,
        the lifecycle manager's expiry loop and take-back -- so a new
        tenure is noticed by whichever looks first, and tracking is rebuilt
        before anything can act on it.

        Only code on discovery's loop may come here: a new tenure rebuilds state
        that loop owns.

        The first look is not a new tenure: tracking is still empty then, and a
        standalone gateway, which always holds, never sees one at all. A loss
        and regain shorter than `check_interval_s` can fall between two looks
        and go unseen. With the defaults a reported server was last seen well
        within its TTL at that point; a TTL below the refresh interval plus the
        check interval is not covered.
        """
        holds = self._may_manage()
        began = holds and self._holding is False
        self._holding = holds
        if not holds:
            # The next tenure takes the fleet back as it is then.
            self._took_back_restored = False
        elif began:
            self._begin_tenure()
        return holds

    def _begin_tenure(self) -> None:
        """Rebuild tracking from the fleet as it is now, when this instance takes the lease back (#2111).

        What was tracked in an earlier tenure was last seen before it ended. Its
        TTL ran out while this instance followed, and the first expiry check in
        the new tenure would deregister every server before a cycle could see
        it. It may also be out of date: a peer may have removed a server, or an
        operator re-pointed it over the API. So it is dropped and the fleet
        taken back, each server seen now, and removal waits a full TTL from
        taking the lease, as it does for a server restored at boot.

        Without a fleet to take back from, what is tracked is seen now instead.
        """
        if self.restored_servers is None:
            self._lifecycle_manager.refresh_all()
            return
        for mcp_server in self._lifecycle_manager.drop_all():
            self._validator.record_deregistration(mcp_server)
        self._take_back_restored()

    def is_tracking(self, name: str) -> bool:
        """Whether discovery governs *name* in this process."""
        return self._lifecycle_manager.get_mcp_server(name) is not None

    async def start(self) -> None:
        """Start the discovery orchestrator."""
        # Captured while we are certain to be on the loop discovery runs on, so a
        # synchronous manual scan can be scheduled back onto it later.
        self._loop = asyncio.get_running_loop()

        if not self.config.enabled:
            logger.info("Discovery is disabled in configuration")
            return

        if self._running:
            logger.warning("Discovery orchestrator already running")
            return

        self._running = True

        # Set up lifecycle manager callback
        self._lifecycle_manager.on_deregister = self._handle_deregister

        # Start components
        await self._discovery_service.start()
        await self._lifecycle_manager.start()

        # Start discovery loop
        self._discovery_task = asyncio.create_task(self._discovery_loop())

        logger.info(f"Discovery orchestrator started (refresh_interval={self.config.refresh_interval_s}s)")

    async def stop(self) -> None:
        """Stop the discovery orchestrator."""
        self._running = False

        # Cancel discovery loop
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
            self._discovery_task = None

        # Stop components
        await self._lifecycle_manager.stop()
        await self._discovery_service.stop()

        logger.info("Discovery orchestrator stopped")

    def request_stop(self) -> None:
        """Tell the sources a stop is coming, from the thread that is stopping.

        Synchronous on purpose. `stop_discovery_loop` calls it before it
        schedules `stop()` on discovery's loop, because a source can block that
        loop's thread, and then `stop()` cannot run until the source returns
        (#1436).
        """
        self._discovery_service.request_stop()

    async def _discovery_loop(self) -> None:
        """Main discovery loop.

        Every cycle asks whether this instance may manage. Discovery registers
        and deregisters servers in storage every replica shares, so three of
        these running at once is three sources of truth arguing -- a server
        registered by one and deregistered by another, in the same second,
        forever.

        A follower keeps looping and keeps asking. It costs a comparison per
        interval and it means the moment this instance takes the lease it starts
        converging, without a restart.
        """
        # Initial discovery
        if self._holds_the_lease():
            await self.run_discovery_cycle()

        while self._running:
            try:
                await asyncio.sleep(self.config.refresh_interval_s)
                if self._running and self._holds_the_lease():
                    await self.run_discovery_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 -- fault-barrier: discovery loop error must not crash background task
                logger.error(f"Error in discovery loop: {e}")
                main_metrics.record_discovery_error(source_type="orchestrator", error_type=type(e).__name__)

    def _holds_the_lease(self) -> bool:
        """The gate, and the line that says when it is closed.

        The gate is right: a follower that ran discovery would deregister
        servers off a view it does not own. What was wrong is that it closed in
        silence -- no log, no metric, nothing. A replica set where the
        discovery-configured replicas are not the one holding the lease
        discovers *nothing*, and every replica has already logged
        `discovery_started` with its source count, which reads as "watching".
        Measured on a two-replica deployment: the configured source ran zero
        cycles until the holder was killed and the other replica took over.

        Paced with the same policy the tailer and the keeper use: the first
        skipped cycle is worth a line, the hundredth is not, and the resumption
        is worth one -- "it started working again" being the fact an operator
        most often has to establish from absence.
        """
        if self._manages():
            skipped = self._idle.run_length
            if self._idle.recovered():
                logger.info(
                    "discovery_resumed_on_this_instance",
                    detail="this instance now holds the management lease and is running discovery again",
                )
                self._trace_lease_transition(Discovery.HOLDER, skipped)
            return True
        if self._idle.failed():
            logger.info(
                "discovery_idle_not_the_lease_holder",
                skipped_cycles=self._idle.run_length,
                refresh_interval_s=self.config.refresh_interval_s,
                detail=(
                    "discovery is configured on this instance but another one holds the management lease, "
                    "so nothing here is discovering. Exactly one instance runs it; if none does, the fleet "
                    "is not converging"
                ),
            )
        if self._idle.run_length == 1:
            self._trace_lease_transition(Discovery.FOLLOWER, None)
        return False

    def _trace_lease_transition(self, role: str, skipped_cycles: int | None) -> None:
        """One short span when this instance changes lease role (#1296).

        Only on the change, never per skipped cycle, and never a
        `discovery.cycle`: a follower's skip is not a failed cycle.
        """
        with get_tracer(__name__).start_as_current_span("discovery.lease_transition") as span:
            span.set_attribute(Discovery.LEASE_ROLE, role)
            if skipped_cycles is not None:
                span.set_attribute(Discovery.SKIPPED_CYCLES, skipped_cycles)

    async def run_discovery_cycle(self) -> DiscoveryCycleResult:
        """Run a single discovery cycle.

        Returns:
            DiscoveryCycleResult with cycle statistics
        """
        import time

        start_time = time.perf_counter()
        self._take_back_restored()

        result = DiscoveryCycleResult()
        tracer = get_tracer(__name__)

        with tracer.start_as_current_span("discovery.cycle") as cycle_span:
            try:
                # Run discovery on all sources
                cycle_result = await self._discovery_service.run_discovery_cycle()
                result.discovered_count = cycle_result.discovered_count
                result.source_results = cycle_result.source_results
                cycle_span.set_attribute("discovery.discovered_count", result.discovered_count)

                # Process discovered mcp_servers through validation. A server an
                # authoritative source lost this cycle is left alone: processing
                # it refreshes its last sighting, so its TTL could never run out
                # and the lifecycle manager would never deregister it.
                for mcp_server in self._discovery_service.get_registered_mcp_servers().values():
                    if mcp_server.name in cycle_result.lost_names:
                        continue
                    validation_result = await self._process_mcp_server(mcp_server)

                    if validation_result == "registered":
                        result.registered_count += 1
                    elif validation_result == "updated":
                        result.updated_count += 1
                    elif validation_result == "quarantined":
                        result.quarantined_count += 1

                # Check for deregistrations
                result.deregistered_count = cycle_result.deregistered_count
                result.error_count = cycle_result.error_count

            except Exception as e:  # noqa: BLE001 -- fault-barrier: cycle failure must not crash orchestrator
                logger.error(f"Discovery cycle failed: {e}")
                result.error_count += 1
                main_metrics.record_discovery_error(source_type="orchestrator", error_type=type(e).__name__)
                record_handled_failure(cycle_span, e)

            # Calculate duration
            duration_seconds = time.perf_counter() - start_time
            result.duration_ms = duration_seconds * 1000

            cycle_span.set_attribute("discovery.registered_count", result.registered_count)
            cycle_span.set_attribute("discovery.quarantined_count", result.quarantined_count)
            cycle_span.set_attribute("discovery.error_count", result.error_count)
            cycle_span.set_attribute("discovery.duration_ms", round(result.duration_ms, 2))

        self._last_cycle = datetime.now(UTC)

        # Update main metrics for unified observability
        for source in self._discovery_service.get_all_sources():
            source_count = result.source_results.get(source.source_type, 0)
            main_metrics.record_discovery_cycle(
                source_type=source.source_type,
                duration=duration_seconds,
                discovered=source_count,
                registered=result.registered_count,
                quarantined=result.quarantined_count,
            )

        # At INFO, not debug: this is the count that answers "did anything
        # actually join the fleet". While it was invisible by default, the only
        # cycle summary an operator saw was the service's, whose "registered"
        # means something else entirely -- so a run that registered nothing
        # still read as success (#771).
        logger.info(
            "discovery_cycle_complete",
            discovered=result.discovered_count,
            registered=result.registered_count,
            updated=result.updated_count,
            quarantined=result.quarantined_count,
            errors=result.error_count,
            duration_ms=round(result.duration_ms, 2),
        )

        return result

    async def _process_mcp_server(self, mcp_server: DiscoveredMcpServer) -> str:
        """Process a discovered mcp_server through validation.

        Args:
            mcp_server: McpServer to process

        Returns:
            Status string: "registered", "updated", "quarantined", "skipped", "rejected"
        """
        # Check if already tracked
        existing = self._lifecycle_manager.get_mcp_server(mcp_server.name)
        if existing:
            if existing.fingerprint == mcp_server.fingerprint or self._refused_before(existing, mcp_server):
                # Just update last_seen
                self._lifecycle_manager.update_seen(mcp_server.name)
                return "skipped"

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("discovery.process_mcp_server") as prov_span:
            prov_span.set_attribute("discovery.mcp_server_name", mcp_server.name)
            prov_span.set_attribute("discovery.source_type", mcp_server.source_type)

            # Validate command from untrusted discovery sources
            refusal = self._command_refusal(mcp_server)
            if refusal is not None:
                if existing:
                    self._keep_running_definition(existing, mcp_server, f"command rejected: {refusal}", final=True)
                else:
                    logger.warning(
                        "discovered_mcp_server_command_rejected",
                        mcp_server_name=mcp_server.name,
                        source=mcp_server.source_type,
                        command=mcp_server.connection_info.get("command", []),
                        reason=refusal,
                    )
                prov_span.set_attribute("discovery.result", "rejected")
                return "rejected"

            # Validate mcp_server
            # The source that produced it answers for its own policy; the
            # validator no longer recognises sources by name.
            producing_source = self._discovery_service.get_source(mcp_server.source_type)
            validation_report = await self._validator.validate(
                mcp_server,
                source=producing_source,
                # A takeover by another source is a new server for that source's count.
                replacing=existing is not None and existing.source_type == mcp_server.source_type,
            )

            main_metrics.record_discovery_validation_duration(
                source_type=mcp_server.source_type,
                duration=validation_report.duration_ms / 1000,
            )
            prov_span.set_attribute("discovery.validation_passed", validation_report.is_passed)

            if not validation_report.is_passed:
                return self._refuse(existing, mcp_server, validation_report, prov_span)

            if existing and self.on_update:
                return await self._apply_change(existing, mcp_server, prov_span)

            # Recorded before registration is attempted, for two reasons. The
            # log has to read in the order things happened -- discovery saw it,
            # then the control plane took it -- and putting this after
            # `on_register` produced a stream whose first row was the
            # registration and whose second was the discovery that caused it.
            # And a server the control plane then refuses is still a server this
            # source reported: the absence of a registration after this row is
            # exactly what an operator needs to see.
            if existing is None:
                self._emit(
                    McpServerDiscovered(
                        mcp_server_name=mcp_server.name,
                        source_type=mcp_server.source_type,
                        mode=mcp_server.mode,
                        fingerprint=mcp_server.fingerprint,
                    )
                )

            # Register with main registry
            if self.on_register:
                try:
                    success = await self.on_register(mcp_server)
                    if not success:
                        logger.warning(f"Control plane rejected mcp_server: {mcp_server.name}")
                        prov_span.set_attribute("discovery.result", "skipped")
                        return "skipped"
                except Exception as e:  # noqa: BLE001 -- fault-barrier: registration callback failure must not crash discovery
                    logger.error(f"Error registering mcp_server {mcp_server.name}: {e}")
                    prov_span.set_attribute("discovery.result", "skipped")
                    record_handled_failure(prov_span, e)
                    return "skipped"

            # Track in lifecycle manager
            if existing:
                self._lifecycle_manager.update_mcp_server(mcp_server)
                main_metrics.record_discovery_registration(source_type=mcp_server.source_type)
                prov_span.set_attribute("discovery.result", "updated")
                return "updated"
            else:
                self._lifecycle_manager.add_mcp_server(mcp_server)
                self._validator.record_registration(mcp_server)
                main_metrics.record_discovery_registration(source_type=mcp_server.source_type)
                prov_span.set_attribute("discovery.result", "registered")
                return "registered"

    def _refuse(
        self, existing: DiscoveredMcpServer | None, mcp_server: DiscoveredMcpServer, validation_report: Any, span: Any
    ) -> str:
        """Quarantine or skip *mcp_server*, which failed validation."""
        # Handle validation failure. A changed definition is logged once
        # (below) and retried: a health check fails while a new pod
        # starts. The running definition stays until one passes.
        if not existing:
            logger.warning(f"McpServer '{mcp_server.name}' failed validation: {validation_report.reason}")

        main_metrics.record_discovery_validation_failure(
            source_type=mcp_server.source_type,
            validation_type=validation_report.result.value,
        )

        if self.config.security.quarantine_on_failure:
            # Only the transition is an event. A refused server is
            # re-reported by its source on every cycle and refused again
            # each time, so recording each refusal writes a row per
            # cycle -- 2880 a day at the default refresh, all saying the
            # same thing. That is the poll transcript this log declines
            # to keep for cycle events, and it would be no better here.
            was_quarantined = self._lifecycle_manager.is_quarantined(mcp_server.name)
            self._lifecycle_manager.quarantine(mcp_server, validation_report.reason)
            if existing:
                # Quarantine untracks the name; the running definition
                # is tracked again, and runs until this one is approved.
                self._keep_running_definition(existing, mcp_server, validation_report.reason, final=False)
            main_metrics.record_discovery_quarantine(reason=validation_report.result.value)
            # A refusal is the discovery event most worth keeping: it
            # says something asked to join the fleet and was turned away,
            # and by which rule.
            if not was_quarantined:
                self._emit(
                    McpServerQuarantined(
                        mcp_server_name=mcp_server.name,
                        source_type=mcp_server.source_type,
                        reason=validation_report.reason,
                        validation_result=validation_report.result.value,
                    )
                )
            span.set_attribute("discovery.result", "quarantined")
            return "quarantined"

        if existing:
            self._keep_running_definition(existing, mcp_server, validation_report.reason, final=False)
        span.set_attribute("discovery.result", "skipped")
        return "skipped"

    def _command_refusal(self, mcp_server: DiscoveredMcpServer) -> str | None:
        """Why the input validator refuses the command of *mcp_server*, or None."""
        command = mcp_server.connection_info.get("command", [])
        if not command or not self._input_validator:
            return None
        try:
            validation_result = self._input_validator.validate_command(command)
        except ValueError as exc:
            return str(exc)
        if not validation_result.valid:
            return "; ".join(i.message for i in validation_result.issues)
        return None

    def _refused_before(self, existing: DiscoveredMcpServer, mcp_server: DiscoveredMcpServer) -> bool:
        """Record a changed definition once, and say whether it was already refused for good (#2091).

        Recorded here because this is the only place both fingerprints exist: a
        moment later `existing` has been overwritten with the new definition
        and the old one is gone for good. Once per definition: a change that is
        not applied is seen again every cycle.
        """
        if self._change_announced.get(mcp_server.name) != mcp_server.fingerprint:
            self._change_announced[mcp_server.name] = mcp_server.fingerprint
            self._emit(
                McpServerDiscoveryConfigChanged(
                    mcp_server_name=mcp_server.name,
                    source_type=mcp_server.source_type,
                    old_fingerprint=existing.fingerprint,
                    new_fingerprint=mcp_server.fingerprint,
                )
            )
        # Refused for good, and logged when it was: the running definition
        # stays, and stays tracked so its TTL does not run out.
        return self._change_refused.get(mcp_server.name) == mcp_server.fingerprint

    async def _apply_change(self, existing: DiscoveredMcpServer, mcp_server: DiscoveredMcpServer, span: Any) -> str:
        """Replace the running definition of *existing* with *mcp_server*, which passed validation (#2091)."""
        assert self.on_update is not None
        try:
            await self.on_update(mcp_server)
        except DiscoveryChangeRefused as e:
            self._keep_running_definition(existing, mcp_server, str(e), final=True)
            span.set_attribute("discovery.result", "rejected")
            return "rejected"
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a failed update callback must not crash discovery
            self._keep_running_definition(existing, mcp_server, f"{type(e).__name__}: {e}", final=False)
            span.set_attribute("discovery.result", "skipped")
            record_handled_failure(span, e)
            return "skipped"
        self._applied(existing, mcp_server)
        main_metrics.record_discovery_registration(source_type=mcp_server.source_type)
        span.set_attribute("discovery.result", "updated")
        return "updated"

    def _applied(self, replaced: DiscoveredMcpServer, mcp_server: DiscoveredMcpServer) -> None:
        """Track *mcp_server* as the definition that runs, and forget its pending change."""
        self._lifecycle_manager.update_mcp_server(mcp_server)
        if replaced.source_type != mcp_server.source_type:
            # Taken over by another source: it moves between the two counts.
            self._validator.record_deregistration(replaced)
            self._validator.record_registration(mcp_server)
        if self._lifecycle_manager.is_quarantined(mcp_server.name):
            # An older change still waiting there would otherwise be approvable
            # over this one.
            self._lifecycle_manager.reject(mcp_server.name)
        self._change_refused.pop(mcp_server.name, None)
        self._change_logged.pop(mcp_server.name, None)
        logger.info(
            "discovery_change_applied",
            mcp_server_name=mcp_server.name,
            source=mcp_server.source_type,
            fingerprint=mcp_server.fingerprint,
        )

    def _keep_running_definition(
        self, existing: DiscoveredMcpServer, mcp_server: DiscoveredMcpServer, reason: str, *, final: bool
    ) -> None:
        """A changed definition was not applied: the one that runs stays, and stays tracked (#2091).

        Tracked again and seen now, or its TTL would run out and the lifecycle
        manager would deregister a server its source still reports. *final*
        means asking again cannot help, so this definition is not tried again.
        Logged once per definition and reason, at DEBUG after that.
        """
        self._lifecycle_manager.add_mcp_server(existing.with_updated_seen_time())
        if final:
            self._change_refused[mcp_server.name] = mcp_server.fingerprint
        logged = (mcp_server.fingerprint, reason)
        log = logger.debug if self._change_logged.get(mcp_server.name) == logged else logger.warning
        self._change_logged[mcp_server.name] = logged
        log(
            "discovery_change_not_applied",
            mcp_server_name=mcp_server.name,
            source=mcp_server.source_type,
            running_fingerprint=existing.fingerprint,
            refused_fingerprint=mcp_server.fingerprint,
            reason=reason,
            retried=not final,
            detail="the definition that was running keeps running" if final else "tried again on the next cycle",
        )

    async def _handle_deregister(self, name: str, reason: str) -> None:
        """Handle mcp_server deregistration.

        Args:
            name: McpServer name
            reason: Reason for deregistration
        """
        mcp_server = self._lifecycle_manager.get_mcp_server(name)
        # Forget it in the resolver as well. An entry left there is processed
        # again next cycle as a server nobody tracks, and registered anew: the
        # removed server came straight back.
        self._conflict_resolver.deregister(name)
        for pending_change in (self._change_announced, self._change_refused, self._change_logged):
            pending_change.pop(name, None)
        if mcp_server:
            self._validator.record_deregistration(mcp_server)
            main_metrics.record_discovery_deregistration(source_type=mcp_server.source_type, reason=reason)
            # Emitted before the callback rather than after: the callback is
            # what removes the server, and if it throws, the barrier below turns
            # that into a log line. The record of *why* it was dropped --
            # ttl_expired, source_removed -- would be the first thing lost.
            self._emit(
                McpServerDiscoveryLost(
                    mcp_server_name=name,
                    source_type=mcp_server.source_type,
                    reason=reason,
                )
            )

        if self.on_deregister:
            try:
                await self.on_deregister(name, reason)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: deregister callback failure must not crash lifecycle
                logger.error(f"Error in deregister callback for {name}: {e}")

    # Public API for tools

    async def trigger_discovery(self) -> dict[str, Any]:
        """Trigger immediate discovery cycle.

        A manual scan also tries again every change that was refused for good
        (#2091): it is what an operator runs after fixing whatever refused it,
        which leaves the definition, and so its fingerprint, unchanged.

        Refused on an instance that does not hold the management lease, and
        run on discovery's own loop, for the reasons `approve_mcp_server` gives.

        Returns:
            Discovery results
        """
        self._refuse_unless_managing("run a discovery scan")
        return cast(dict[str, Any], await self._on_discovery_loop(self._trigger))

    async def _trigger(self) -> dict[str, Any]:
        self._change_refused.clear()
        result = await self.run_discovery_cycle()
        return result.to_dict()

    def _refuse_unless_managing(self, what: str) -> None:
        """Refuse a manual scan or approval on an instance that does not hold the management lease.

        Only the periodic loop asked; an ex-holder keeps its lifecycle and
        quarantine state, and a scan or approval sent to it would rewrite rows
        from a view that is no longer the fleet's (#2091). A standalone gateway
        is always its own manager.
        """
        # A read, not the tenure observer: this runs on the caller's thread,
        # before the hop onto discovery's loop, and a new tenure rebuilds state
        # that loop owns. The next gate on that loop observes it (#2111).
        if not self._may_manage():
            raise self._not_here(what)

    @staticmethod
    def _not_here(what: str) -> McpServerNotHereError:
        return McpServerNotHereError(
            f"cannot {what} here: this instance does not hold the management lease, and discovery runs only on "
            "the instance that does. Send the request to that instance."
        )

    async def _on_discovery_loop(self, work: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """Await *work* on discovery's own loop when it runs one elsewhere, inline otherwise."""
        loop = self._loop
        if loop is not None and loop.is_running() and loop is not asyncio.get_running_loop():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(work(), loop))
        return await work()

    def trigger_discovery_blocking(self) -> dict[str, Any]:
        """Run one discovery cycle to completion from a synchronous caller.

        The command bus runs handlers in a worker thread (run_in_threadpool), so
        a handler cannot ``await``. Before this existed the scan handler simply
        called ``trigger_discovery()`` and dropped the coroutine on the floor --
        Python logged "coroutine 'trigger_discovery' was never awaited", no scan
        ran, and the endpoint answered 200 with a fabricated count of 0.

        When discovery is started it runs on a dedicated long-lived loop in its
        own thread; the scan is scheduled back onto that loop with
        ``run_coroutine_threadsafe`` -- the same bridge lifecycle uses for
        start()/stop() -- so a manual scan and the periodic cycle serialise on
        one loop rather than mutating DiscoveryService state from two. When
        discovery was never started (unit tests, stdio without the discovery
        thread) there is no such loop, so the cycle runs on a private one.

        Returns:
            The cycle result dict (``DiscoveryCycleResult.to_dict()``), whose
            server count lives under ``discovered_count``.
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            return asyncio.run_coroutine_threadsafe(self.trigger_discovery(), loop).result()
        return asyncio.run(self.trigger_discovery())

    def get_pending_mcp_servers(self) -> list[DiscoveredMcpServer]:
        """Get mcp_servers pending registration.

        Returns:
            List of pending mcp_servers
        """
        return self._discovery_service.get_pending_mcp_servers()

    def get_quarantined(self) -> dict[str, dict[str, Any]]:
        """Get quarantined mcp_servers with reasons.

        Returns:
            Dictionary of name -> {mcp_server, reason, quarantine_time}
        """
        quarantined = self._lifecycle_manager.get_quarantined()
        return {
            name: {
                "mcp_server": mcp_server.to_dict(),
                "reason": reason,
                "quarantine_time": qtime.isoformat(),
            }
            for name, (mcp_server, reason, qtime) in quarantined.items()
        }

    async def approve_mcp_server(self, name: str) -> dict[str, Any]:
        """Approve a quarantined or pending mcp_server.

        A server waits in one of two places: quarantine, where it failed
        validation, and pending, where it passed but ``auto_register`` is off.
        ``hangar_discovered`` lists the second kind, and approval only looked
        in the first, so with ``auto_register: false`` nothing could ever
        register a discovered server (#1667).

        A changed definition of a server that already runs waits in the same
        two places, while the running definition keeps running (#2091). Its
        approval replaces the running server; refused, the running definition
        stays and the change is not approved.

        Refused on an instance that does not hold the management lease. Run on
        discovery's own loop when it has one, as a manual scan is: the replace
        waits for calls in flight, which must not freeze the request loop that
        asked, and a cycle replacing the same server at the same time is then
        not possible.

        Args:
            name: McpServer name

        Returns:
            Result dictionary
        """
        self._refuse_unless_managing(f"approve mcp_server {name!r}")
        return cast(dict[str, Any], await self._on_discovery_loop(lambda: self._approve(name)))

    async def _approve(self, name: str) -> dict[str, Any]:
        """Approve *name* on the loop discovery runs on; see `approve_mcp_server`."""
        # Asked again here, through the tenure observer: the lease may have
        # changed hands while the request hopped onto this loop, and a new
        # tenure rebuilds tracking before the approval reads it (#2111).
        if not self._manages():
            raise self._not_here(f"approve mcp_server {name!r}")
        running = self._lifecycle_manager.get_mcp_server(name)
        mcp_server = self._lifecycle_manager.approve(name)
        was_pending = False
        if mcp_server is None:
            mcp_server = self._discovery_service.approve_pending(name)
            was_pending = mcp_server is not None

        if mcp_server and running and self.on_update and running.fingerprint != mcp_server.fingerprint:
            return await self._approve_change(running, mcp_server, was_pending)

        if mcp_server:
            # Register with main registry
            if self.on_register:
                try:
                    await self.on_register(mcp_server)
                except Exception as e:  # noqa: BLE001 -- fault-barrier: registration callback failure must not crash approval
                    logger.error(f"Error registering approved mcp_server {name}: {e}")
                    return {"approved": False, "mcp_server": name, "error": str(e)}

            if was_pending:
                # Tracked from here on, so a later cycle sees it unchanged and its TTL runs.
                self._lifecycle_manager.add_mcp_server(mcp_server)
            self._validator.record_registration(mcp_server)
            main_metrics.record_discovery_registration(source_type=mcp_server.source_type)

            return {"approved": True, "mcp_server": name, "status": "registered"}

        return {
            "approved": False,
            "mcp_server": name,
            "error": "McpServer not found in quarantine or pending",
        }

    async def _approve_change(
        self, running: DiscoveredMcpServer, mcp_server: DiscoveredMcpServer, was_pending: bool
    ) -> dict[str, Any]:
        """Apply an approved change of a running server, or put everything back as it was."""
        assert self.on_update is not None
        try:
            await self.on_update(mcp_server)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: a failed update callback must not crash approval
            logger.error(f"Error applying approved change of mcp_server {mcp_server.name}: {e}")
            self._lifecycle_manager.add_mcp_server(running)
            if was_pending:
                # Back to the running definition, so the next cycle parks the
                # change in pending again rather than applying it unapproved.
                self._conflict_resolver.register(running)
            return {"approved": False, "mcp_server": mcp_server.name, "error": str(e)}
        self._applied(running, mcp_server)
        main_metrics.record_discovery_registration(source_type=mcp_server.source_type)
        return {"approved": True, "mcp_server": mcp_server.name, "status": "updated"}

    async def reject_mcp_server(self, name: str) -> dict[str, Any]:
        """Reject a quarantined or pending mcp_server.

        Args:
            name: McpServer name

        Returns:
            Result dictionary
        """
        mcp_server = self._lifecycle_manager.reject(name) or self._discovery_service.reject_pending(name)

        if mcp_server:
            return {"rejected": True, "mcp_server": name}

        return {
            "rejected": False,
            "mcp_server": name,
            "error": "McpServer not found in quarantine or pending",
        }

    async def get_sources_status(self) -> list[dict[str, Any]]:
        """Get status of all discovery sources.

        Returns:
            List of source status dictionaries
        """
        statuses = await self._discovery_service.get_sources_status()

        # Update main metrics for each source
        for status in statuses:
            main_metrics.update_discovery_source(
                source_type=status.source_type,
                mode=status.mode.value,
                is_healthy=status.is_healthy,
                mcp_servers_count=status.mcp_servers_count,
            )

        return [s.to_dict() for s in statuses]

    def get_stats(self) -> dict[str, Any]:
        """Get orchestrator statistics.

        Returns:
            Statistics dictionary
        """
        lifecycle_stats = self._lifecycle_manager.get_stats()

        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "refresh_interval_s": self.config.refresh_interval_s,
            "sources_count": len(self._discovery_service.get_all_sources()),
            **lifecycle_stats,
        }
