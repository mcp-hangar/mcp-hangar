"""`mcp-hangar why` reads the store the gateway writes for the config (#2085).

A config that names no event store has the gateway write ``data/events.db``
under its working directory. `why` read the path `init` writes into the configs
it generates instead, so it found nothing, or another store, while the refusals
were recorded where the gateway put them.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from mcp_hangar.domain.events import ToolCallRefused
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore
from mcp_hangar.server.cli.main import app
from mcp_hangar.server.cli.services.config_file import default_event_store_path


def _write_refusal(db: Path, tool: str) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    SQLiteEventStore(str(db)).append_at_end(
        "refusals", [ToolCallRefused(mcp_server_id="fs", tool_name=tool, gate="tool_access", gate_reason="deny_list")]
    )


def _why(config: Path):
    return CliRunner().invoke(app, ["--config", str(config), "why"], terminal_width=200)


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The directory the gateway ran in, with the store it wrote; and another store where `init` would put one."""
    run_dir = tmp_path / "run"
    _write_refusal(run_dir / "data" / "events.db", "gwtool")
    _write_refusal(default_event_store_path(), "xdgtool")
    monkeypatch.chdir(run_dir)
    return run_dir


@pytest.mark.parametrize("config", [{"mcp_servers": {}}, {"event_store": {}, "mcp_servers": {}}])
def test_a_config_that_names_no_store_is_read_from_data_events_db(workdir: Path, config: dict) -> None:
    path = workdir / "config.yaml"
    path.write_text(yaml.safe_dump(config))

    result = _why(path)

    assert result.exit_code == 0, result.output
    assert "gwtool" in result.output
    assert "xdgtool" not in result.output


def test_no_config_file_reads_data_events_db_too(workdir: Path) -> None:
    result = _why(workdir / "absent.yaml")

    assert result.exit_code == 0, result.output
    assert "gwtool" in result.output


def test_the_path_init_writes_is_still_read_when_the_config_names_it(workdir: Path) -> None:
    path = workdir / "config.yaml"
    path.write_text(yaml.safe_dump({"event_store": {"path": str(default_event_store_path())}, "mcp_servers": {}}))

    result = _why(path)

    assert result.exit_code == 0, result.output
    assert "xdgtool" in result.output


def test_a_missing_relative_store_says_it_is_read_from_the_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"mcp_servers": {}}))

    result = _why(path)

    assert result.exit_code == 1
    message = " ".join(result.output.split())
    assert f"No event store at data{os.sep}events.db" in message
    assert "current one" in message
