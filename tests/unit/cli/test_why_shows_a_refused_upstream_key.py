"""`mcp-hangar why` lists a refused upstream key, cold or warm, with both keys (#1942, ADR-030 §6).

A cold start refused for the upstream's key is a `ToolCallRefused`. A warm call
refused for it is a `ToolInvocationFailed` with `failure_code`, read as what it
is and never re-labelled. Every other failed invocation stays out of the list.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from mcp_hangar.domain.events import ToolCallRefused, ToolInvocationFailed
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.refusal_log import read_refusals

COLD_KEY = "sha256:" + "a" * 64
WARM_KEY = "sha256:" + "b" * 64
LISTED = "sha256:" + "c" * 64


def _store(tmp_path: Path) -> Path:
    db = tmp_path / "events.db"
    SQLiteEventStore(str(db)).append_at_end(
        "calls",
        [
            ToolCallRefused(
                mcp_server_id="payments",
                tool_name="charge",
                gate="cold_start",
                gate_reason="upstream_peer_key_mismatch",
                observed_peer_spki=COLD_KEY,
            ),
            ToolInvocationFailed(mcp_server_id="payments", tool_name="charge", error_type="TimeoutError"),
            ToolInvocationFailed(
                mcp_server_id="payments",
                tool_name="refund",
                error_type="UpstreamPeerKeyMismatchError",
                failure_code="upstream_peer_key_mismatch",
                observed_peer_spki=WARM_KEY,
            ),
        ],
    )
    return db


def test_both_refusals_are_listed_and_an_ordinary_failure_is_not(tmp_path) -> None:
    refusals = read_refusals(_store(tmp_path))

    assert [(r.event_type, r.tool, r.reason, r.observed_peer_spki) for r in refusals] == [
        ("ToolInvocationFailed", "refund", "upstream_peer_key_mismatch", WARM_KEY),
        ("ToolCallRefused", "charge", "upstream_peer_key_mismatch", COLD_KEY),
    ]


def test_the_command_prints_the_presented_key_and_the_key_list_configured_now(tmp_path) -> None:
    db = _store(tmp_path)
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "event_store": {"path": str(db)},
                "mcp_servers": {
                    "payments": {
                        "mode": "remote",
                        "endpoint": "https://payments:8443",
                        "tls": {"peer_identity": {"key_ref": "k", "spki_sha256": [LISTED]}},
                    }
                },
            }
        )
    )

    result = CliRunner().invoke(app, ["--config", str(config), "why"], terminal_width=300)

    assert result.exit_code == 0, result.output
    # The console wraps at spaces; read the text without its line breaks.
    flat = result.output.replace("\n", "")
    assert f"upstream presented {WARM_KEY}" in flat
    assert f"upstream presented {COLD_KEY}" in flat
    assert f"listed now {LISTED}" in flat
    assert "TimeoutError" not in flat


def test_without_a_key_list_in_the_config_it_says_so(tmp_path) -> None:
    result = CliRunner().invoke(app, ["why", "--db", str(_store(tmp_path))], terminal_width=300)

    assert result.exit_code == 0, result.output
    assert "(no key list in this config)" in result.output.replace("\n", "")
