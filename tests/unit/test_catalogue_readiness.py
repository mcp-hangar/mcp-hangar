from types import SimpleNamespace
import threading
from unittest.mock import Mock

from mcp_hangar.server import catalogue_readiness as catalogue
from mcp_hangar.server.lifecycle import build_readiness_report


def test_first_discovery_gates_ready_but_later_outage_does_not(monkeypatch):
    monkeypatch.setenv("MCP_REQUIRED_CATALOGUE", "notes,memory")
    monkeypatch.setattr(catalogue, "_discovered", set())
    repo = SimpleNamespace(get_all=lambda: {}, count=lambda: 0)
    assert build_readiness_report(repo)[1] == 503
    catalogue.record_discovered("notes")
    assert build_readiness_report(repo)[0]["catalogue_missing"] == ["memory"]
    catalogue.record_discovered("memory")
    assert build_readiness_report(repo)[1] == 200


def test_failed_start_is_retried_without_invoking_tools(monkeypatch):
    monkeypatch.setenv("MCP_REQUIRED_CATALOGUE", "notes")
    monkeypatch.setattr(catalogue, "_discovered", set())
    stop = threading.Event()
    attempts = []

    def send(command):
        attempts.append(command)
        if len(attempts) == 1:
            raise ConnectionError("upstream unavailable")
        stop.set()

    monkeypatch.setattr(stop, "wait", lambda _: None)
    catalogue.reconcile_catalogue(SimpleNamespace(command_bus=SimpleNamespace(send=send)), stop)
    assert len(attempts) == 2
    assert all(type(c).__name__ == "StartMcpServerCommand" for c in attempts)
    assert catalogue.missing_servers() == []


def test_stopped_reconciler_does_not_start_anything(monkeypatch):
    monkeypatch.setenv("MCP_REQUIRED_CATALOGUE", "notes")
    stop = threading.Event()
    stop.set()
    runtime = Mock()
    catalogue.reconcile_catalogue(runtime, stop)
    runtime.command_bus.send.assert_not_called()
