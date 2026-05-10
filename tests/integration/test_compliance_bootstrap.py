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


def test_bogus_format_returns_none():
    assert _create_compliance_exporter("bogus", None) is None
