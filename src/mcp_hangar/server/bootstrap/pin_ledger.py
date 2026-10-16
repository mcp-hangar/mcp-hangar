"""Record the configuration's pins in the event log at boot (#1800)."""

from __future__ import annotations

from typing import Any

from ...application.read_models.tool_projection import get_tool_projection_registry
from ...application.services.pin_ledger import reconcile_pin_ledger
from ...logging_config import get_logger

logger = get_logger(__name__)


def record_configured_pins(event_bus: Any) -> None:
    """Append the pins that are new, changed or gone since the log last heard of them."""
    try:
        reconcile_pin_ledger(get_tool_projection_registry(), event_bus)
    except Exception:  # noqa: BLE001 -- fault-barrier: an unwritable ledger must not stop the gateway serving
        logger.warning("pin_ledger_not_recorded", exc_info=True)
