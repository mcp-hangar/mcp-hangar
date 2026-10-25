"""A backup of a private file is private too (#2048).

`pin --write` kept the previous config as `<config>.bak`, and `init` copied the
client's MCP config into its state directory, both written under the umask --
0644 -- whatever the original's mode. A client config often holds other servers'
tokens, and the gateway's config is created 0600 (#1653).
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from mcp_hangar.server.cli.services.init_manifest import remember_original, state_dir
from mcp_hangar.server.cli.services.pinning import write_config_with_pins


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture(autouse=True)
def _umask_022():
    import os

    previous = os.umask(0o022)
    yield
    os.umask(previous)


def test_pin_writes_a_private_backup_of_a_private_config(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    config.chmod(0o600)

    backup = write_config_with_pins(config, {"mcp_servers": {}})

    assert backup.read_text() == "mcp_servers: {}\n"
    assert _mode(backup) == 0o600


def test_init_backs_up_a_private_client_config_privately(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path))
    client = tmp_path / "client.json"
    client.write_text('{"mcpServers": {"x": {"env": {"TOKEN": "t"}}}}')
    client.chmod(0o600)

    backup = remember_original(client)

    assert backup is not None and backup.read_bytes() == client.read_bytes()
    assert _mode(backup) == 0o600
    assert _mode(backup.parent) == 0o700
    assert backup.parent.parent == state_dir()


def test_a_shared_original_keeps_its_mode(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    config.chmod(0o644)

    backup = write_config_with_pins(config, {"mcp_servers": {}})

    assert _mode(backup) == 0o644
