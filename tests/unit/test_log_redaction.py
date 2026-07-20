"""Tests for value-level secret redaction in the logging pipeline and log buffer.

Covers the structlog processor (_redact_secret_values), the shared redactor
accessor, and the stderr-redaction contract used by the log buffer / /logs API.
"""

from __future__ import annotations

from mcp_hangar.domain.security.redactor import get_default_redactor
from mcp_hangar.logging_config import _redact_secret_values

AWS = "AKIAIOSFODNN7EXAMPLE"
GHP = "ghp_" + "a" * 36
JWT = "eyJ" + "a" * 60 + "." + "b" * 60


def test_default_redactor_is_shared_and_no_long_string_scan() -> None:
    r1 = get_default_redactor()
    r2 = get_default_redactor()
    assert r1 is r2  # process-wide singleton
    # Long-string redaction is off: a normal long value is preserved verbatim.
    normal = "a perfectly normal sentence with lots of words and no secrets at all"
    assert r1.redact(normal) == normal


def test_default_redactor_scrubs_known_token_shapes() -> None:
    r = get_default_redactor()
    for secret in (AWS, GHP, JWT):
        out = r.redact(f"connecting with {secret} now")
        assert secret not in out
        assert "REDACTED" in out


def test_processor_scrubs_message_and_nested_values() -> None:
    event = {
        "event": f"launching server with {AWS}",
        "config": {"github": GHP, "note": "safe text"},
        "tokens": [JWT, "not-a-secret"],
        "count": 7,
    }
    out = _redact_secret_values(None, "info", event)
    assert AWS not in out["event"]
    assert GHP not in str(out["config"])
    assert JWT not in str(out["tokens"])
    # Non-secret content and non-strings are preserved.
    assert out["config"]["note"] == "safe text"
    assert "not-a-secret" in out["tokens"]
    assert out["count"] == 7


def test_processor_is_depth_bounded() -> None:
    # Deeply nested structures must not blow the stack.
    deep: dict = {"event": "x"}
    node = deep
    for _ in range(20):
        node["child"] = {}
        node = node["child"]
    node["secret"] = AWS
    # Should return without error (values past the depth cap are left as-is).
    out = _redact_secret_values(None, "info", deep)
    assert out["event"] == "x"


def test_stderr_line_redaction_contract() -> None:
    # The stderr reader redacts each line before it enters the buffer; this is
    # the exact transform applied at mcp_server._reader.
    r = get_default_redactor()
    line = f"[boot] AWS_ACCESS_KEY_ID={AWS} token={GHP}"
    redacted = r.redact(line)
    assert AWS not in redacted and GHP not in redacted
