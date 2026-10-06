import pytest

from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.server.bootstrap.event_handlers import _create_compliance_exporter


def test_cef_format_creates_exporter(monkeypatch, tmp_path):
    output_path = tmp_path / "cef.log"
    monkeypatch.setenv("MCP_COMPLIANCE_FORMAT", "cef")
    monkeypatch.setenv("MCP_COMPLIANCE_OUTPUT", str(output_path))

    exporter = _create_compliance_exporter("cef", str(output_path))

    assert exporter is not None
    exporter.export_tool_invocation(
        mcp_server_id="math",
        tool_name="add",
        status="success",
        duration_ms=10.0,
    )
    assert "CEF:" in output_path.read_text()


def test_bogus_format_refuses(tmp_path):
    # It returned None, and bootstrap served calls with no export (#1701).
    with pytest.raises(ConfigurationError, match="Unknown MCP_COMPLIANCE_FORMAT 'bogus'"):
        _create_compliance_exporter("bogus", None)


def test_an_output_that_cannot_be_appended_to_refuses(tmp_path):
    with pytest.raises(ConfigurationError, match="cannot be appended to"):
        _create_compliance_exporter("cef", str(tmp_path / "missing" / "cef.log"))
