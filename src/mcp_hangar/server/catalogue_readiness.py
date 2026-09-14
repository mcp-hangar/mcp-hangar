"""Opt-in per-replica catalogue bootstrap gate, independent of backend health.

A required server must have been discovered once before a replica receives
traffic. A later upstream outage does not evict every gateway from its Service.
The reconciler keeps retrying cold/failed servers without invoking any tool.
"""

import os
import threading

_lock = threading.Lock()
_discovered: set[str] = set()


def required_servers() -> set[str]:
    return {name.strip() for name in os.environ.get("MCP_REQUIRED_CATALOGUE", "").split(",") if name.strip()}


def record_discovered(name: str) -> None:
    with _lock:
        _discovered.add(name)


def missing_servers() -> list[str]:
    with _lock:
        return sorted(required_servers() - _discovered)


def reconcile_catalogue(runtime, stop: threading.Event) -> None:
    from ..application.commands import StartMcpServerCommand
    from ..logging_config import get_logger

    logger = get_logger(__name__)
    while not stop.is_set():
        for name in sorted(required_servers()):
            if stop.is_set():
                return
            try:
                runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=name))
                record_discovered(name)
            except Exception as exc:  # noqa: BLE001 -- a failed backend must be retried independently
                # No exception text: provider errors can contain credentials.
                logger.warning("catalogue_reconcile_failed", mcp_server_id=name, error_type=type(exc).__name__)
        stop.wait(30)
