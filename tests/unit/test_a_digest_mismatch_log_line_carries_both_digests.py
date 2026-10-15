"""The `domain_event` line for a digest mismatch carries both digests (#1696).

`DigestMismatchEvent` has `expected_digest` and `observed_digest`, but the log
line is a summary of identifiers and left them out, so an operator could not read
the value to pin from the log. A digest is a hash of a schema, an identifier like
the ids already in the line, so it belongs in the summary; free text and the
caller's identity still do not.
"""

from __future__ import annotations

from structlog.testing import capture_logs

from mcp_hangar.application.event_handlers.logging_handler import LoggingEventHandler
from mcp_hangar.domain.events import DigestMismatchEvent

EXPECTED = "sha256:" + "a" * 64
OBSERVED = "sha256:" + "b" * 64


def _line(event):
    with capture_logs() as logs:
        LoggingEventHandler().handle(event)
    return next(entry for entry in logs if entry["event"] == "domain_event")


def test_both_digests_are_in_the_line():
    line = _line(
        DigestMismatchEvent(
            mcp_server_id="math",
            tool_name="add",
            expected_digest=EXPECTED,
            observed_digest=OBSERVED,
            enforcement="warn",
            correlation_id="corr-1",
        )
    )

    assert line["expected_digest"] == EXPECTED
    assert line["observed_digest"] == OBSERVED
    assert (line["mcp_server_id"], line["tool_name"], line["event_type"]) == ("math", "add", "DigestMismatchEvent")


def test_a_tool_with_no_pin_has_no_expected_digest_key():
    line = _line(
        DigestMismatchEvent(
            mcp_server_id="math",
            tool_name="add",
            expected_digest=None,
            observed_digest=OBSERVED,
            enforcement="audit",
            correlation_id="corr-1",
        )
    )

    assert "expected_digest" not in line
    assert line["observed_digest"] == OBSERVED
