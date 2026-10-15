"""`mcp-hangar why` prints the refusals in the event store, read-only (#1777).

After a first deny a local user had nothing to look at. The events are written
by the production `SQLiteEventStore`, in another process that keeps the store
open, and read by the real command through `main.app`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.refusal_log import read_refusals

_WRITER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from mcp_hangar.domain.events import (
        DigestMismatchEvent, ToolApprovalDenied, ToolCallRefused, ToolInvocationCompleted,
    )
    from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

    store = SQLiteEventStore(sys.argv[1])
    store.append_at_end("refusals", [
        ToolInvocationCompleted(mcp_server_id="fs", tool_name="read", correlation_id="ok"),
        ToolCallRefused(mcp_server_id="fs", tool_name="write", gate="tool_access", gate_reason="deny_list"),
        ToolApprovalDenied(approval_id="a1", mcp_server_id="db", tool_name="drop", decided_by="alice", reason="no"),
        DigestMismatchEvent(
            mcp_server_id="fs", tool_name="read", expected_digest="sha256:" + "a" * 8,
            observed_digest="sha256:" + "b" * 8, enforcement="block", correlation_id="c-1",
        ),
    ])
    Path(sys.argv[2]).write_text("ready")
    time.sleep(120)  # keeps the process, and so the store, open
    """
)


@pytest.fixture
def store(tmp_path: Path):
    """An event store that another process wrote and still holds open."""
    db = tmp_path / "events.db"
    ready = tmp_path / "ready"
    proc = subprocess.Popen([sys.executable, "-c", _WRITER, str(db), str(ready)], env=dict(os.environ))
    try:
        deadline = time.monotonic() + 30
        while not ready.exists():
            assert proc.poll() is None, "the writer exited before it was ready"
            assert time.monotonic() < deadline, "the writer never became ready"
            time.sleep(0.05)
        yield db
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _why(*args: str):
    return CliRunner().invoke(app, ["why", *args], terminal_width=200)


def test_the_latest_refusal_is_the_digest_mismatch_with_both_digests(store):
    result = _why("--db", str(store), "--last", "1")

    assert result.exit_code == 0, result.output
    assert "digest_pin" in result.output and "write" not in result.output
    assert "sha256:aaaaaaaa" in result.output and "sha256:bbbbbbbb" in result.output


def test_every_kind_of_refusal_is_listed_and_a_completed_call_is_not(store):
    refusals = read_refusals(store, last=50)

    assert [r.event_type for r in refusals] == ["DigestMismatchEvent", "ToolApprovalDenied", "ToolCallRefused"]
    output = _why("--db", str(store)).output
    assert all(r.event_id[:8] in output for r in refusals)
    assert "tool_access" in output and "deny_list" in output
    assert "denied by alice" in output


def test_server_filters(store):
    result = _why("--db", str(store), "--server", "db")

    assert "drop" in result.output and "fs" not in result.output


def test_one_event_is_shown_in_full_by_its_id_prefix(store):
    listing = _why("--db", str(store), "--server", "db")
    assert listing.exit_code == 0

    import sqlite3

    row = (
        sqlite3.connect(f"file:{store}?mode=ro", uri=True)
        .execute("SELECT json_extract(data, '$.event_id') FROM events WHERE event_type = 'ToolApprovalDenied'")
        .fetchone()
    )
    result = _why("--db", str(store), row[0][:8])

    assert result.exit_code == 0, result.output
    assert "ToolApprovalDenied" in result.output and "decided_by: alice" in result.output


def test_reading_writes_nothing_to_the_store(store):
    before = {p.name: p.stat().st_mtime_ns for p in store.parent.glob("events.db")}
    size = store.stat().st_size

    result = _why("--db", str(store))

    assert result.exit_code == 0, result.output
    assert {p.name: p.stat().st_mtime_ns for p in store.parent.glob("events.db")} == before
    assert store.stat().st_size == size


def test_the_store_comes_from_the_config(store, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"event_store": {"path": str(store)}, "mcp_servers": {}}))

    result = CliRunner().invoke(app, ["--config", str(config), "why", "--last", "1"], terminal_width=200)

    assert result.exit_code == 0, result.output
    assert "sha256:bbbbbbbb" in result.output


def test_a_missing_store_says_where_it_looked(tmp_path):
    result = _why("--db", str(tmp_path / "nope.db"))

    assert result.exit_code == 1
    assert "No event store at" in " ".join(result.output.split())


@pytest.mark.parametrize(
    ("config", "needle"),
    [
        ({"persistence": {"backend": "postgresql"}}, "postgresql"),
        ({"event_store": {"enabled": False}}, "disabled"),
        ({"event_store": {"driver": "memory"}}, "not durable"),
    ],
)
def test_a_store_that_is_not_a_file_says_so(tmp_path, config, needle):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))

    result = CliRunner().invoke(app, ["--config", str(path), "why"], terminal_width=200)

    assert result.exit_code == 1
    assert needle in " ".join(result.output.split())
