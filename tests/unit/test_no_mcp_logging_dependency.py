"""Guard test confirming the audit pipeline has no MCP-protocol logging dependency.

SEP-2577 deprecated MCP protocol logging/* methods. Hangar's audit/observability is
event-sourced + OTEL-based and deliberately avoids any MCP protocol logging method
calls (literals "logging/setLevel" and "notifications/message").
"""

from pathlib import Path


def test_no_mcp_protocol_logging_dependency() -> None:
    """Assert that MCP-protocol logging method strings are absent from source."""
    src_dir = Path(__file__).parent.parent.parent / "src" / "mcp_hangar"

    forbidden_strings = ["logging/setLevel", "notifications/message"]

    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for forbidden in forbidden_strings:
            assert forbidden not in content, (
                f"Found MCP-protocol logging string '{forbidden}' in {py_file.relative_to(src_dir.parent.parent)}"
            )
