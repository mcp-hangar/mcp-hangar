"""A configured SIEM export that cannot be built refuses startup (#1701).

Both cases used to log a warning and return None, and bootstrap then served
calls with no export.
"""

from unittest.mock import patch

import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.bootstrap.event_handlers import _create_compliance_exporter

pytestmark = pytest.mark.security


def test_refuses_when_compliance_unavailable():
    with patch.dict("sys.modules", {"mcp_hangar.compliance": None}):
        with pytest.raises(ConfigurationError, match="MCP_COMPLIANCE_FORMAT 'cef' is set, and its exporter cannot"):
            _create_compliance_exporter("cef", None)


def test_unknown_format_refuses():
    with pytest.raises(ConfigurationError, match="Unknown MCP_COMPLIANCE_FORMAT 'bogus'; expected one of: cef, "):
        _create_compliance_exporter("bogus", None)
