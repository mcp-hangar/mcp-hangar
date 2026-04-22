"""Behavioral profiling contracts (ports) for the domain layer.

These protocols define the interfaces for behavioral profiling components.
Enterprise layer provides concrete implementations; MIT core ships with
NullBehavioralProfiler (always DISABLED).

Contracts:
    IBehavioralProfiler -- Facade for behavioral profiling (mode, observations).
    IBaselineStore -- Persistence for observation baselines and mode state.
    IDeviationDetector -- Compares observations against learned baselines.

Null implementation:
    NullBehavioralProfiler -- No-op implementation for MIT core.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol, TYPE_CHECKING, runtime_checkable

if TYPE_CHECKING:
    from ..value_objects.behavioral import BehavioralMode, NetworkObservation, ResourceSample


@runtime_checkable
class IBehavioralProfiler(Protocol):
    """Facade for behavioral profiling of a mcp_server.

    Manages the profiling mode and routes observations to the appropriate
    subsystem (baseline store in LEARNING, deviation detector in ENFORCING).

    In MIT core, only NullBehavioralProfiler is available (always DISABLED).
    Enterprise module provides a real implementation.
    """

    @abstractmethod
    def get_mode(self, mcp_server_id: str) -> BehavioralMode:
        """Get the current behavioral profiling mode for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.

        Returns:
            Current BehavioralMode for the mcp_server.
        """
        ...

    @abstractmethod
    def set_mode(self, mcp_server_id: str, mode: BehavioralMode) -> None:
        """Set the behavioral profiling mode for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.
            mode: New BehavioralMode to set.
        """
        ...

    @abstractmethod
    def record_observation(self, observation: NetworkObservation) -> list[dict[str, Any]]:
        """Record a network observation from a mcp_server.

        In LEARNING mode, stores the observation for baseline building and
        returns an empty list.
        In ENFORCING mode, checks against baseline for deviations and returns
        a list of deviation dicts.
        In DISABLED mode, this is a no-op returning an empty list.

        Args:
            observation: The network observation to record.

        Returns:
            List of deviation dicts. Empty list when no deviations detected
            or when not in ENFORCING mode.
        """
        ...


@runtime_checkable
class IBaselineStore(Protocol):
    """Persistence for observation baselines and mode state.

    Stores aggregated network observations during the LEARNING phase
    and provides baseline data for the ENFORCING phase.
    """

    @abstractmethod
    def record_observation(self, observation: NetworkObservation) -> None:
        """Store an observation for baseline building.

        Args:
            observation: The network observation to store.
        """
        ...

    @abstractmethod
    def get_observations(self, mcp_server_id: str) -> list[dict[str, Any]]:
        """Retrieve baseline observation records for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.

        Returns:
            List of observation records as dicts.
        """
        ...

    @abstractmethod
    def get_mode(self, mcp_server_id: str) -> BehavioralMode:
        """Get the persisted behavioral mode for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.

        Returns:
            Current persisted BehavioralMode.
        """
        ...

    @abstractmethod
    def set_mode(
        self,
        mcp_server_id: str,
        mode: BehavioralMode,
        learning_duration_hours: int = 72,
    ) -> None:
        """Persist the behavioral mode with timing metadata.

        Args:
            mcp_server_id: Identifier of the mcp_server.
            mode: New BehavioralMode to persist.
            learning_duration_hours: Duration of the learning phase in hours.
        """
        ...


@runtime_checkable
class IDeviationDetector(Protocol):
    """Compares network observations against a learned baseline.

    Returns a list of detected deviations when an observation does not
    match the expected behavioral profile.
    """

    @abstractmethod
    def check_observation(self, observation: NetworkObservation) -> list[dict[str, Any]]:
        """Check an observation against the baseline.

        Args:
            observation: The network observation to check.

        Returns:
            List of deviation dicts. Empty list means no deviation detected.
        """
        ...


class NullBehavioralProfiler:
    """No-op behavioral profiler. Always returns DISABLED mode.

    Used when enterprise behavioral profiling is not installed.
    Satisfies the IBehavioralProfiler protocol at runtime.
    """

    def get_mode(self, mcp_server_id: str) -> BehavioralMode:
        """Return DISABLED for any mcp_server -- profiling not active."""
        from ..value_objects.behavioral import BehavioralMode

        return BehavioralMode.DISABLED

    def set_mode(self, mcp_server_id: str, mode: BehavioralMode) -> None:
        """No-op -- profiling mode cannot be changed without enterprise module."""
        pass

    def record_observation(self, observation: NetworkObservation) -> list[dict[str, Any]]:
        """No-op -- observations are discarded without enterprise module.

        Returns:
            Empty list (no deviations possible without enterprise module).
        """
        return []


@runtime_checkable
class IResourceStore(Protocol):
    """Persistence for time-series resource usage samples and computed baselines.

    Stores CPU, memory, and network I/O samples during both LEARNING and
    ENFORCING phases. Computes statistical baselines (mean + stddev) from
    accumulated samples. Enterprise layer provides a SQLite-backed implementation.
    """

    @abstractmethod
    def record_sample(self, sample: ResourceSample) -> None:
        """Persist a resource usage sample.

        Args:
            sample: The resource sample to store.
        """
        ...

    @abstractmethod
    def get_samples(self, mcp_server_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieve recent resource samples for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.
            limit: Maximum number of samples to return (most recent first).

        Returns:
            List of sample records as dicts, ordered by sampled_at descending.
        """
        ...

    @abstractmethod
    def get_baseline(self, mcp_server_id: str) -> dict[str, Any] | None:
        """Retrieve the computed resource baseline for a mcp_server.

        Args:
            mcp_server_id: Identifier of the mcp_server.

        Returns:
            Baseline dict with mean/stddev statistics, or None if not computed.
        """
        ...

    @abstractmethod
    def compute_and_store_baseline(self, mcp_server_id: str) -> dict[str, Any] | None:
        """Compute and persist a resource baseline from accumulated samples.

        Requires a minimum number of samples (typically 10) to produce
        a meaningful baseline. Returns None if insufficient data.

        Args:
            mcp_server_id: Identifier of the mcp_server.

        Returns:
            Baseline dict with mean/stddev statistics, or None if insufficient data.
        """
        ...

    @abstractmethod
    def prune(self, retention_days: int = 7) -> int:
        """Delete resource samples older than retention period.

        Args:
            retention_days: Number of days to retain samples (default 7).

        Returns:
            Number of rows deleted.
        """
        ...


@runtime_checkable
class IResourceMonitor(Protocol):
    """Background monitor collecting resource usage from mcp_server containers.

    Polls Docker containers or K8s pods for CPU, memory, and network I/O
    metrics. Enterprise layer provides a real implementation; MIT core
    ships with NullResourceMonitor.
    """

    @abstractmethod
    def start(self) -> None:
        """Start the resource monitor background thread."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the resource monitor background thread."""
        ...

    @property
    @abstractmethod
    def running(self) -> bool:
        """Whether the monitor is currently running."""
        ...


class NullResourceMonitor:
    """No-op resource monitor. Always reports not running.

    Used when enterprise resource monitoring is not installed.
    Satisfies the IResourceMonitor protocol at runtime.
    """

    def start(self) -> None:
        """No-op -- resource monitoring not available without enterprise module."""

    def stop(self) -> None:
        """No-op -- nothing to stop."""

    @property
    def running(self) -> bool:
        """Always False -- monitor is never active without enterprise module."""
        return False
