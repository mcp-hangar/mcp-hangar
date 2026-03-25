"""BehavioralProfiler facade for behavioral profiling -- BSL 1.1 licensed.

Coordinates baseline storage and mode management for provider behavioral
profiling. Delegates to BaselineStore for persistence and routes observations
based on the current profiling mode.

See enterprise/LICENSE.BSL for license terms.
"""

from typing import Any

import structlog

from mcp_hangar.domain.value_objects.behavioral import BehavioralMode, NetworkObservation

logger = structlog.get_logger(__name__)


class BehavioralProfiler:
    """Facade for behavioral profiling of a provider.

    Manages the profiling mode and routes observations to the appropriate
    subsystem. In LEARNING mode, delegates observations to the BaselineStore.
    In DISABLED mode, observations are discarded. In ENFORCING mode,
    observations are checked against the baseline via IDeviationDetector.

    Satisfies the ``IBehavioralProfiler`` protocol defined in
    ``mcp_hangar.domain.contracts.behavioral``.

    Args:
        baseline_store: Store for persisting observations and mode state.
        config: Optional configuration dict with keys like
            ``learning_duration_hours`` (default 72).
    """

    def __init__(self, baseline_store: "IBaselineStore", config: dict | None = None) -> None:
        self._baseline_store = baseline_store
        self._config = config or {}
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
        In ENFORCING mode, returns an empty list (deviation detection wiring
        is done at a higher level in Phase 44 plan 03).

        Args:
            observation: The network observation to record.

        Returns:
            List of deviation dicts. Empty in LEARNING and DISABLED modes.
        """
        mode = self._baseline_store.get_mode(observation.provider_id)
        if mode == BehavioralMode.LEARNING:
            self._baseline_store.record_observation(observation)
        return []
