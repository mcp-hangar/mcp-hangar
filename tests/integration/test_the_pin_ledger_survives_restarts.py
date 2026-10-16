"""The history of a tool's pin is in the event log, across restarts and replicas (#1800).

Pins are declared in the configuration. Each boot runs the real ``bootstrap()``
in its own interpreter over one SQLite event store: the first records the pins,
a restart with the same pins records nothing, a changed digest records a new
line that names the one it replaced, and a removed pin is revoked. The history
is then read from the log alone -- no configuration, no gateway -- by the
library and by ``mcp-hangar why --tool``, which is what a second replica reading
the same store sees.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands import why as why_module
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.refusal_log import read_pin_history

HARNESS = Path(__file__).with_name("_pin_ledger_harness.py")
OLD, NEW = "a" * 64, "b" * 64


def _boot(db: Path, pins: dict[str, str]) -> None:
    spec = db.parent / "pins.json"
    spec.write_text(json.dumps(pins))
    result = subprocess.run(
        [sys.executable, str(HARNESS), str(db), str(spec)], capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr[-3000:]


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "events.db"


def _history(db: Path, tool: str = "add"):
    return [(e.event, e.sha256, e.previous_sha256) for e in read_pin_history(db, tool)]


def test_the_first_boot_records_the_pin(db):
    _boot(db, {"add": OLD})

    assert _history(db) == [("recorded", OLD, None)]
    (entry,) = read_pin_history(db, "add")
    assert (entry.server, entry.tenant_id, entry.approved_by) == ("math", None, "config")


def test_a_restart_with_the_same_pins_records_nothing_more(db):
    _boot(db, {"add": OLD})
    _boot(db, {"add": OLD})
    _boot(db, {"add": OLD})

    assert _history(db) == [("recorded", OLD, None)]


def test_a_changed_digest_is_a_new_line_naming_the_one_it_replaced(db):
    _boot(db, {"add": OLD})
    _boot(db, {"add": NEW})

    assert _history(db) == [("recorded", OLD, None), ("recorded", NEW, OLD)]


def test_a_removed_pin_is_revoked(db):
    _boot(db, {"add": OLD})
    _boot(db, {})

    assert _history(db) == [("recorded", OLD, None), ("revoked", OLD, None)]


def test_a_pin_that_comes_back_is_recorded_again_without_a_predecessor(db):
    _boot(db, {"add": OLD})
    _boot(db, {})
    _boot(db, {"add": OLD})

    assert _history(db) == [("recorded", OLD, None), ("revoked", OLD, None), ("recorded", OLD, None)]


def test_each_tool_has_its_own_history(db):
    _boot(db, {"add": OLD, "echo": NEW})

    assert _history(db, "add") == [("recorded", OLD, None)]
    assert _history(db, "echo") == [("recorded", NEW, None)]
    assert _history(db, "never-pinned") == []


def test_the_history_is_read_from_the_log_alone_by_the_command(db, monkeypatch):
    _boot(db, {"add": OLD})
    _boot(db, {"add": NEW})
    # The table folds at the width of the terminal, which CI does not have.
    monkeypatch.setattr(why_module, "console", Console(width=240))

    result = CliRunner().invoke(app, ["why", "--db", str(db), "--tool", "add"])

    assert result.exit_code == 0, result.output
    assert OLD[:12] in result.output and NEW[:12] in result.output
    assert result.output.count("recorded") == 2 and "config" in result.output


def test_a_tool_with_no_pin_says_so(db, monkeypatch):
    _boot(db, {"add": OLD})
    monkeypatch.setattr(why_module, "console", Console(width=240))

    result = CliRunner().invoke(app, ["why", "--db", str(db), "--tool", "echo"])

    assert result.exit_code == 0 and "No pin recorded" in " ".join(result.output.split())
