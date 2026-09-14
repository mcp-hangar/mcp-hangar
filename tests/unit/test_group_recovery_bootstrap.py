from types import SimpleNamespace
from unittest.mock import MagicMock

from mcp_hangar.domain.events import HealthCheckPassed
from mcp_hangar.server.bootstrap import cqrs


def test_preloaded_and_reloaded_groups_receive_health_recovery(monkeypatch):
    group = MagicMock()
    group.members = [SimpleNamespace(id="weather")]
    context = SimpleNamespace(groups={"pool": group})
    monkeypatch.setattr(cqrs, "get_context", lambda: context)
    monkeypatch.setattr(cqrs, "get_saga_manager", MagicMock())
    monkeypatch.setattr(cqrs, "set_group_rebalance_saga", MagicMock())
    cqrs.init_saga({})
    event = HealthCheckPassed(mcp_server_id="weather", duration_ms=1)
    context.group_rebalance_saga.handle(event)
    group.report_success.assert_called_once_with("weather")
    replacement = MagicMock()
    replacement.members = [SimpleNamespace(id="weather")]
    context.groups["pool"] = replacement
    context.group_rebalance_saga.handle(event)
    replacement.report_success.assert_called_once_with("weather")
