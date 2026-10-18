"""``/config/diff`` and ``/config/backup`` act on the file the gateway runs on (#1652).

Both accepted a ``config_path`` and loaded or copied whatever it named, which
contradicted the hardening that refuses it on ``/config/reload``. Without it
they resolved ``MCP_CONFIG`` or ``./config.yaml`` and ignored ``--config``, so a
gateway started on one file backed up and diffed another.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.server.bootstrap.composition import set_running_config_path


@pytest.fixture
def client():
    from mcp_hangar.server.api.router import create_api_router

    ctx = Mock()
    ctx.auth_components = None
    ctx.runtime = None
    with (
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.config.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.config.serialize_full_config", return_value={"mcp_servers": {}}),
    ):
        yield TestClient(create_api_router(auth_components=None), raise_server_exceptions=False)
    set_running_config_path(None)


def _write(path: Path, text: str) -> str:
    path.write_text(text)
    return str(path)


def _servers(*ids: str) -> str:
    return "mcp_servers:\n" + "".join(f"  {i}:\n    mode: subprocess\n    command: [x]\n" for i in ids)


def test_a_backup_is_of_the_file_the_gateway_was_started_with(client, tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    (other / "config.yaml").write_text("decoy: true\n")
    running = _write(tmp_path / "running.yaml", "mcp_servers: {}\n")
    set_running_config_path(running)

    response = client.post("/config/backup")

    assert response.status_code == 200, response.text
    assert response.json()["path"].endswith("running.yaml.bak1")
    assert not (other / "config.yaml.bak1").exists()


def test_a_backup_refuses_a_caller_supplied_path(client, tmp_path):
    set_running_config_path(_write(tmp_path / "running.yaml", "mcp_servers: {}\n"))
    target = _write(tmp_path / "secret.txt", "not a config\n")

    response = client.post("/config/backup", json={"config_path": target})

    assert response.status_code == 422, response.text
    assert "config_path" in response.text
    assert not Path(target + ".bak1").exists(), "the refused path was copied anyway"


def test_a_diff_is_against_the_file_the_gateway_was_started_with(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text(_servers("decoy"))
    set_running_config_path(_write(tmp_path / "running.yaml", _servers("marker_in_the_running_file")))

    body = client.get("/config/diff").json()

    assert "marker_in_the_running_file" in str(body["on_disk"])
    assert "decoy" not in str(body["on_disk"])


def test_a_diff_refuses_a_caller_supplied_path(client, tmp_path):
    set_running_config_path(_write(tmp_path / "running.yaml", "a: 1\n"))
    target = _write(tmp_path / "secret.yaml", _servers("private_marker"))

    response = client.get("/config/diff", params={"config_path": target})

    assert response.status_code == 422, response.text
    assert "private_marker" not in response.text


def test_a_gateway_with_no_config_file_keeps_the_old_default(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    (tmp_path / "config.yaml").write_text("mcp_servers: {}\n")
    set_running_config_path(None)

    assert client.post("/config/backup").json()["path"].endswith("config.yaml.bak1")
