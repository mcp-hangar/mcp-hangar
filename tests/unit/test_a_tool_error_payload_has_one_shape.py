"""Every error payload a tool answers with names the failure under `error_type` (#1509).

#1495 gave every MCP tool error one payload -- `error`, `error_type`, `details`,
built by `ToolErrorPayload.to_dict()` -- and moved the key naming the failure
from `type` to `error_type`. Two producers were outside that change:

* `MCPError.to_dict()`, a second serializer in the domain that named the class
  under `type`. Nothing called it: the REST envelope builds `code`/`message`/
  `details` from the exception's own attributes, and no tool path reached it. It
  is deleted rather than corrected, and this file pins that -- a serializer with
  no callers is exactly what drifts out of agreement unnoticed, which is how it
  came to disagree with the one shape in the first place.
* `hangar_reload_config`, whose fault barrier answered `status`/`message`/
  `error_type`: the right key in a fourth layout.

The guard below is tree-wide rather than scoped to `server/tools/`, because the
producer that drifted lived in `domain/`, not under the tools package. It reads
the source rather than calling the producers, so a payload built by a path no
test drives is still caught.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import Mock, patch

from mcp_hangar.domain.exceptions import MCPError
from mcp_hangar.server.tools.hangar import hangar_reload_config

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "mcp_hangar"

#: A dict literal naming one of these is an error payload, whatever else it holds.
ERROR_PAYLOAD_KEYS = {"error", "error_type", "details", "message"}


def _payloads_naming_a_failure_under_type() -> list[str]:
    """Every dict literal in `src/` that is error-shaped and still carries `type`."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - nothing in src should fail to parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "type" in keys and keys & ERROR_PAYLOAD_KEYS:
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno} {sorted(keys)}")
    return offenders


class TestNoProducerNamesAFailureUnderType:
    def test_no_error_payload_in_src_carries_a_type_key(self):
        offenders = _payloads_naming_a_failure_under_type()
        assert offenders == [], (
            f"{len(offenders)} error payload(s) name the failure under `type`. Every error a tool can "
            f"answer with uses `error_type`, the key `hangar_call` already used (#1495): {offenders}"
        )


class TestTheDomainErrorHasNoSecondSerializer:
    """`MCPError` carries the fields; the surface that answers decides the shape."""

    def test_mcp_error_has_no_to_dict(self):
        assert not hasattr(MCPError, "to_dict"), (
            "MCPError.to_dict() is a second error serializer with no callers. The REST envelope "
            "builds `code`/`message`/`details` from the exception, and a tool error is "
            "ToolErrorPayload's three keys -- neither reads this."
        )

    def test_the_fields_an_embedder_reads_are_still_there(self):
        exc = MCPError("boom", mcp_server_id="store", operation="lookup", details={"k": "v"})

        assert (exc.message, exc.mcp_server_id, exc.operation, exc.details) == ("boom", "store", "lookup", {"k": "v"})


class TestReloadAnswersTheOneShape:
    """`hangar_reload_config`'s fault barrier answers what every other tool error does."""

    def _context(self) -> Mock:
        context = Mock()
        context.runtime.command_bus.send.return_value = {
            "mcp_servers_added": ["store"],
            "mcp_servers_removed": [],
            "mcp_servers_updated": [],
            "mcp_servers_unchanged": ["ledger"],
            "duration_ms": 12.5,
        }
        return context

    def test_a_failed_reload_answers_error_error_type_details(self):
        context = self._context()
        context.runtime.command_bus.send.side_effect = RuntimeError("configuration file is unreadable")

        with patch("mcp_hangar.server.tools.hangar.get_context", return_value=context):
            result = hangar_reload_config()

        assert result == {
            "error": "Configuration reload failed: configuration file is unreadable",
            "error_type": "RuntimeError",
            "details": {},
        }

    def test_a_failed_reload_no_longer_answers_status_or_message(self):
        context = self._context()
        context.runtime.command_bus.send.side_effect = ValueError("bad value")

        with patch("mcp_hangar.server.tools.hangar.get_context", return_value=context):
            result = hangar_reload_config()

        assert "status" not in result
        assert "message" not in result
        assert "type" not in result

    def test_a_successful_reload_is_unchanged(self):
        context = self._context()

        with patch("mcp_hangar.server.tools.hangar.get_context", return_value=context):
            result = hangar_reload_config(graceful=False)

        assert result == {
            "status": "success",
            "message": "Configuration reloaded successfully",
            "mcp_servers_added": ["store"],
            "mcp_servers_removed": [],
            "mcp_servers_updated": [],
            "mcp_servers_unchanged": ["ledger"],
            "duration_ms": 12.5,
        }
