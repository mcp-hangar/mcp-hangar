"""The client entry `init` writes starts Hangar with no PATH and from any directory (#1772).

Two things made it start only from the installing shell:

* the entry was the bare name ``mcp-hangar``. A GUI client spawns it with the
  desktop session's PATH -- no virtualenv, no pipx or uv bin directory -- and
  where PEP 668 forces one of those installs, that is exactly the PATH that does
  not contain it;
* the generated config left the event store at ``data/events.db``, relative to
  the working directory the client chose, so a read-only directory made Hangar
  refuse to start.

Either way the client reported "Connection closed" and nothing else. This writes
the entry and the config through the code `init` uses, then spawns exactly what
was written -- command, arguments, config -- through the SDK's stdio client, the
way a client would, with a PATH holding neither and a read-only working
directory.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_hangar.server.cli.services.config_file import ConfigFileManager, default_event_store_path
from mcp_hangar.server.cli.services.mcp_clients import HANGAR_ENTRY_NAME, client_by_key, write_hangar_entry

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX PATH and directory permissions")

SPAWN_PATH = "/usr/bin:/bin"


def _written_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, Path, Path]:
    """What `init` writes: the client's entry, the Hangar config, and where its event store is."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))

    config_path = tmp_path / "home" / ".config" / "mcp-hangar" / "config.yaml"
    manager = ConfigFileManager(config_path)
    manager.config_path.parent.mkdir(parents=True)
    config_path.write_text(manager.generate_initial_config([], {}, deps=None), encoding="utf-8")

    client = client_by_key("cursor", cwd=tmp_path)
    assert client is not None
    write_hangar_entry(client, config_path)
    entry = json.loads(client.path.read_text(encoding="utf-8"))["mcpServers"][HANGAR_ENTRY_NAME]
    return entry, config_path, state / "mcp-hangar" / "events.db"


async def _initialize_and_list(entry: dict, cwd: Path) -> list[str]:
    params = StdioServerParameters(command=entry["command"], args=entry["args"], env={"PATH": SPAWN_PATH}, cwd=cwd)
    with anyio.fail_after(60):
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            return sorted(tool.name for tool in (await session.list_tools()).tools)


def test_the_written_entry_names_a_file_not_a_command_on_the_path(tmp_path, monkeypatch) -> None:
    entry, config_path, _ = _written_entry(tmp_path, monkeypatch)

    assert Path(entry["command"]).is_absolute(), entry["command"]
    assert Path(entry["command"]).exists()
    assert entry["args"][-3:] == ["--config", str(config_path), "serve"]


def test_the_written_config_keeps_the_event_store_in_the_state_directory(tmp_path, monkeypatch) -> None:
    _, config_path, store = _written_entry(tmp_path, monkeypatch)

    assert default_event_store_path() == store
    assert json.dumps(str(store)) in config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("writable", [False, True], ids=["read-only-cwd", "writable-cwd"])
def test_spawned_with_a_bare_path_it_reaches_tools_list_and_writes_nothing_beside_it(
    tmp_path, monkeypatch, writable
) -> None:
    entry, _, store = _written_entry(tmp_path, monkeypatch)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    if not writable:
        cwd.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        tools = anyio.run(_initialize_and_list, entry, cwd)
    finally:
        cwd.chmod(stat.S_IRWXU)

    assert tools, "Hangar answered tools/list with nothing"
    assert list(cwd.iterdir()) == [], "a file was created in the spawning process's working directory"
    assert store.exists(), "the event store was not created where the config says"
    assert (store.parent / "saga_state.db").exists(), "the saga state belongs beside the event store"


def test_the_bare_name_the_entry_used_to_hold_cannot_be_started_from_that_path(tmp_path) -> None:
    """The failure this fixes: the old entry, spawned with the same PATH."""
    old_entry = {"command": HANGAR_ENTRY_NAME, "args": ["serve"]}
    if os.access("/usr/bin/mcp-hangar", os.X_OK) or os.access("/bin/mcp-hangar", os.X_OK):
        pytest.skip("mcp-hangar is installed system-wide on this machine")

    with pytest.raises(BaseException):  # noqa: B017 -- the SDK surfaces the failed spawn as an ExceptionGroup or OSError
        anyio.run(_initialize_and_list, old_entry, tmp_path)
