"""`init --undo` puts the client configs back byte-for-byte (#1771).

`init` wrote a new `<name>.backup.<ts>.json` beside every client file on every
run, so after two runs the newest backup was not the file the user had before
Hangar, and a project-level backup piled up inside the user's repository. There
was no command to undo any of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli.commands.init import app as init_app
from mcp_hangar.server.cli.services.init_manifest import restore, state_dir
from mcp_hangar.server.cli.services.mcp_clients import client_by_key, write_hangar_entry

HANGAR_CONFIG = Path("/home/u/.config/mcp-hangar/config.yaml")

#: Odd on purpose: key order, indentation and a missing trailing newline must all survive.
USER_FILE = b'{\n    "mcpServers": {"mine": {"command": "my-server"}},\n  "theme":"dark"}'


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def _clients(home: Path):
    return [client_by_key(key, cwd=home / "project") for key in ("cursor", "claude-code", "claude-code-project")]


def _seed(home: Path) -> dict[Path, bytes | None]:
    (home / "project").mkdir()
    originals: dict[Path, bytes | None] = {}
    for client in _clients(home):
        if client.key == "claude-code":  # this one does not exist before init
            originals[client.path] = None
            continue
        client.path.parent.mkdir(parents=True, exist_ok=True)
        client.path.write_bytes(USER_FILE)
        originals[client.path] = USER_FILE
    return originals


def _init(home: Path) -> None:
    for client in _clients(home):
        write_hangar_entry(client, HANGAR_CONFIG)


def _state(paths) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def test_init_twice_then_undo_leaves_every_file_as_it_was(home):
    originals = _seed(home)

    _init(home)
    _init(home)
    assert json.loads(next(iter(_clients(home))).path.read_text())["mcpServers"]["mcp-hangar"]

    result = restore()

    assert result is not None and not result.refused
    assert _state(originals) == originals
    assert not (state_dir() / "init-manifest.json").exists()


def test_no_backup_is_written_inside_the_project_or_beside_the_user_files(home):
    originals = _seed(home)

    _init(home)
    _init(home)

    for path in originals:
        assert not list(path.parent.glob("*.backup.*")), path
    assert not list((home / "project").rglob("*.backup.*"))
    assert list((state_dir() / "init-backups").iterdir()), "the originals are kept in the state directory"


def test_the_second_run_keeps_the_first_backup(home):
    _seed(home)
    first = [write_hangar_entry(client, HANGAR_CONFIG) for client in _clients(home)]
    second = [write_hangar_entry(client, HANGAR_CONFIG) for client in _clients(home)]

    assert [b is not None for b in first] == [True, False, True]  # the file that did not exist has no backup
    assert second == [None, None, None]


def test_a_file_edited_after_init_makes_undo_refuse_without_force(home):
    originals = _seed(home)
    _init(home)
    edited = _clients(home)[0].path
    edited.write_text(edited.read_text() + "\n// edited by hand\n")
    before = _state(originals)

    result = restore()

    assert result is not None and [c.path for c in result.refused] == [edited]
    assert "changed since init wrote it" in result.refused[0].summary
    assert _state(originals) == before, "a refused undo changes nothing"
    assert (state_dir() / "init-manifest.json").exists()

    forced = restore(force=True)
    assert forced is not None and not forced.refused
    assert _state(originals) == originals


def test_an_edit_before_a_second_init_still_makes_undo_refuse(home):
    _seed(home)
    _init(home)
    edited = _clients(home)[0].path
    document = json.loads(edited.read_text())
    document["mine-too"] = True
    edited.write_text(json.dumps(document))
    _init(home)

    result = restore()

    assert result is not None and [c.path for c in result.refused] == [edited]


def test_undo_with_nothing_recorded_says_so(home):
    assert restore() is None


def test_the_command_restores_and_reports(home):
    originals = _seed(home)
    _init(home)

    result = CliRunner().invoke(init_app, ["--undo"])

    assert result.exit_code == 0, result.output
    assert "Restored:" in result.output and "Removed:" in result.output
    assert _state(originals) == originals


def test_the_command_refuses_with_a_summary_and_exit_one(home):
    _seed(home)
    _init(home)
    edited = _clients(home)[0].path
    edited.write_text("{}")

    result = CliRunner().invoke(init_app, ["--undo"])

    output = " ".join(result.output.split())  # the console wraps long lines
    assert result.exit_code == 1
    assert "Not restored" in output and "changed since init wrote" in output
    assert "--undo --force" in output


def test_the_command_with_nothing_to_undo_exits_zero(home):
    result = CliRunner().invoke(init_app, ["--undo"])

    assert result.exit_code == 0
    assert "Nothing to undo" in result.output


def test_force_without_undo_is_refused():
    result = CliRunner().invoke(init_app, ["--force"])

    assert result.exit_code == 2
