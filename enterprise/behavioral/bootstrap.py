"""Behavioral profiling bootstrap -- BSL 1.1 licensed.

Creates and configures the BehavioralProfiler with its backing BaselineStore,
DeviationDetector, and optional EventBus integration.
Called from the server bootstrap pipeline when the enterprise module is available.

See enterprise/LICENSE.BSL for license terms.
"""

from typing import Any

import structlog

from mcp_hangar.domain.contracts.behavioral import IBehavioralProfiler

from .baseline_store import BaselineStore
from .deviation_detector import DeviationDetector
from .profiler import BehavioralProfiler

logger = structlog.get_logger(__name__)


def bootstrap_behavioral(
    db_path: str = "data/events.db",
    config: dict | None = None,
    event_bus: Any = None,
) -> IBehavioralProfiler:
    """Create a configured BehavioralProfiler instance.

    Initializes the SQLite-backed BaselineStore, creates a DeviationDetector
    with configuration-driven thresholds, and wraps everything in a
    BehavioralProfiler facade. Logs initialization at INFO level.

    Args:
        db_path: Path to SQLite database file. Use ``":memory:"`` for testing.
        config: Optional behavioral profiling configuration dict with keys:
            - ``learning_duration_hours``: Duration of learning phase (default 72).
            - ``mode``: Default mode string (informational only).
            - ``deviation_detection``: Dict with detector config:
                - ``frequency_threshold_multiplier``: Float (default 3.0).
                - ``min_observations``: Int (default 10).
                - ``severity_overrides``: Dict mapping deviation type to severity.
        event_bus: Optional event bus for publishing BehavioralDeviationDetected
            events from the profiler during ENFORCING mode.

    Returns:
        Configured BehavioralProfiler satisfying IBehavioralProfiler protocol.
    """
    config = config or {}
    store = BaselineStore(db_path=db_path)

    # Create DeviationDetector from config
    detection_config = config.get("deviation_detection", {})
    detector = DeviationDetector(
        baseline_store=store,
        frequency_threshold_multiplier=detection_config.get("frequency_threshold_multiplier", 3.0),
        min_observations=detection_config.get("min_observations", 10),
        severity_overrides=detection_config.get("severity_overrides"),
    )

    profiler = BehavioralProfiler(
        baseline_store=store,
        config=config,
        detector=detector,
        event_bus=event_bus,
    )

    logger.info(
        "behavioral_profiling_initialized",
        db_path=db_path,
        default_mode=config.get("mode", "disabled"),
        detector_threshold=detection_config.get("frequency_threshold_multiplier", 3.0),
        detector_min_obs=detection_config.get("min_observations", 10),
        event_bus_attached=event_bus is not None,
    )

    return profiler
