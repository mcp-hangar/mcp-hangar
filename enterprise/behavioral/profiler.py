"""BehavioralProfiler facade for behavioral profiling -- BSL 1.1 licensed.

Coordinates baseline storage and mode management for provider behavioral
profiling. Delegates to BaselineStore for persistence and routes observations
based on the current profiling mode.

In ENFORCING mode, delegates deviation detection to an injected
IDeviationDetector and publishes BehavioralDeviationDetected events
via an injected EventBus.

See enterprise/LICENSE.BSL for license terms.
"""

from typing import Any

import structlog

from mcp_hangar.domain.contracts.behavioral import IDeviationDetector
from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation

logger = structlog.get_logger(__name__)


class BehavioralProfiler:
    """Facade for behavioral profiling of a provider.

    Manages the profiling mode and routes observations to the appropriate
    subsystem. In LEARNING mode, delegates observations to the BaselineStore.
    In DISABLED mode, observations are discarded. In ENFORCING mode,
    checks observations against baseline via DeviationDetector, stores
    the observation, and publishes BehavioralDeviationDetected events.

    Satisfies the ``IBehavioralProfiler`` protocol defined in
    ``mcp_hangar.domain.contracts.behavioral``.

    Args:
        baseline_store: Store for persisting observations and mode state.
        config: Optional configuration dict with keys like
            ``learning_duration_hours`` (default 72).
        detector: Optional deviation detector for ENFORCING mode. When None
            in ENFORCING mode, observations are stored but no deviations
            are detected (backward compatibility).
        event_bus: Optional event bus for publishing BehavioralDeviationDetected
            events. When None, deviations are returned but not published.
    """

    def __init__(
        self,
        baseline_store: "IBaselineStore",
        config: dict | None = None,
        detector: IDeviationDetector | None = None,
        event_bus: Any = None,
    ) -> None:
        self._baseline_store = baseline_store
        self._config = config or {}
        self._detector = detector
        self._event_bus = event_bus
        self._logger = structlog.get_logger(__name__)

    def get_mode(self, provider_id: str) -> BehavioralMode:
        """Get the current behavioral profiling mode for a provider.

        Args:
            provider_id: Identifier of the provider.

        Returns:
            Current BehavioralMode for the provider.
        """
        return self._baseline_store.get_mode(provider_id)

    def set_mode(self, provider_id: str, mode: BehavioralMode) -> None:
        """Set the behavioral profiling mode for a provider.

        Args:
            provider_id: Identifier of the provider.
            mode: New BehavioralMode to set.
        """
        learning_duration_hours = self._config.get("learning_duration_hours", 72)
        self._baseline_store.set_mode(provider_id, mode, learning_duration_hours)
        self._logger.info(
            "behavioral_mode_changed",
            provider_id=provider_id,
            mode=str(mode),
        )

    def record_observation(self, observation: NetworkObservation) -> list[dict[str, Any]]:
        """Record a network observation from a provider.

        In LEARNING mode, stores the observation for baseline building and
        returns an empty list.
        In DISABLED mode, this is a no-op returning an empty list.
        In ENFORCING mode, checks against baseline FIRST, stores observation
        SECOND, publishes BehavioralDeviationDetected events, returns
        deviation list.

        Args:
            observation: The network observation to record.

        Returns:
            List of deviation dicts. Empty in LEARNING and DISABLED modes,
            or when no deviations detected in ENFORCING mode.
        """
        mode = self._baseline_store.get_mode(observation.provider_id)

        if mode == BehavioralMode.DISABLED:
            return []

        if mode == BehavioralMode.LEARNING:
            self._baseline_store.record_observation(observation)
            return []

        # ENFORCING mode: check FIRST, store SECOND
        deviations: list[dict[str, Any]] = []
        if self._detector is not None:
            deviations = self._detector.check_observation(observation)
        else:
            self._logger.warning(
                "enforcing_mode_without_detector",
                provider_id=observation.provider_id,
            )

        # Store observation after detection check
        self._baseline_store.record_observation(observation)

        # Publish domain events for each deviation
        if deviations and self._event_bus is not None:
            from mcp_hangar.domain.events import BehavioralDeviationDetected

            for d in deviations:
                event = BehavioralDeviationDetected(
                    provider_id=observation.provider_id,
                    deviation_type=d["deviation_type"],
                    observed=d["observed"],
                    baseline_expected=d["baseline_expected"],
                    severity=d["severity"],
                )
                self._event_bus.publish(event)

        if deviations:
            self._logger.debug(
                "deviations_detected",
                provider_id=observation.provider_id,
                count=len(deviations),
                types=[d["deviation_type"] for d in deviations],
            )

        return deviations
