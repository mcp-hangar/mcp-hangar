from unittest.mock import patch

from mcp_hangar.server.bootstrap import event_handlers
from mcp_hangar.server.bootstrap.event_handlers import _create_compliance_exporter


def test_returns_none_when_compliance_unavailable():
    with patch.dict("sys.modules", {"mcp_hangar.compliance": None}):
        with patch.object(event_handlers.logger, "warning") as warning:
            result = _create_compliance_exporter("cef", None)

    assert result is None
    warning.assert_called_once()


def test_unknown_format_returns_none():
    with patch.object(event_handlers.logger, "warning") as warning:
        result = _create_compliance_exporter("bogus", None)

    assert result is None
    warning.assert_called_once()
