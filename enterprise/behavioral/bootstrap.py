"""Behavioral profiling bootstrap -- BSL 1.1 licensed.

Creates and configures the BehavioralProfiler with its backing BaselineStore.
Called from the server bootstrap pipeline when the enterprise module is available.

See enterprise/LICENSE.BSL for license terms.
"""

import structlog

from mcp_hangar.domain.contracts.behavioral import IBehavioralProfiler

from .baseline_store import BaselineStore
from .profiler import BehavioralProfiler

logger = structlog.get_logger(__name__)


def bootstrap_behavioral(
    db_path: str = "data/events.db",
    config: dict | None = None,
) -> IBehavioralProfiler:
    """Create a configured BehavioralProfiler instance.

    Initializes the SQLite-backed BaselineStore and wraps it in a
    BehavioralProfiler facade. Logs initialization at INFO level.

    Args:
        db_path: Path to SQLite database file. Use ``":memory:"`` for testing.
        config: Optional behavioral profiling configuration dict with keys:
            - ``learning_duration_hours``: Duration of learning phase (default 72).
            - ``mode``: Default mode string (informational only).

    Returns:
        Configured BehavioralProfiler satisfying IBehavioralProfiler protocol.
    """
    config = config or {}
    store = BaselineStore(db_path=db_path)
    profiler = BehavioralProfiler(baseline_store=store, config=config)

    logger.info(
        "behavioral_profiling_initialized",
        db_path=db_path,
        default_mode=config.get("mode", "disabled"),
    )

    return profiler
