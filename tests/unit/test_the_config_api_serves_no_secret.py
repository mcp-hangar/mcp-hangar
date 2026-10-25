"""`GET /config` and `GET /config/diff` serve no secret, at any depth (#2029).

Both routes ran a sanitizer over top-level key names only. `GET /config` has one
top-level key, `mcp_servers`, so every stored `env` value was served verbatim.
The diff was computed from the file as loaded -- after `${VAR}` interpolation --
so the real value of an environment secret appeared in `on_disk` and in the
unified `diff` string, while `POST /config/export` redacted the same values.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.server.bootstrap.composition import set_running_config_path

SECRET = "s3cr3t-value-from-the-environment"
LITERAL = "hunter2-literal"
SHAPED = "ghp_" + "A" * 36


def _client(ctx: Mock, in_memory: dict) -> TestClient:
    from mcp_hangar.server.api.router import create_api_router

    return TestClient(create_api_router(auth_components=None), raise_server_exceptions=False), [
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.config.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.config.serialize_full_config", return_value=in_memory),
    ]


@pytest.fixture
def served():
    ctx = Mock()
    ctx.auth_components = None
    ctx.runtime = None

    def make(in_memory: dict | None = None) -> TestClient:
        client, patches = _client(ctx, in_memory if in_memory is not None else {"mcp_servers": {}})
        for p in patches:
            p.start()
        make.patches.extend(patches)
        return client

    make.patches = []
    make.ctx = ctx
    yield make
    for p in make.patches:
        p.stop()
    set_running_config_path(None)


def _no_secret(text: str) -> None:
    for value in (SECRET, LITERAL, SHAPED):
        assert value not in text, f"served {value!r}"


class TestGetConfig:
    def test_a_stored_servers_env_is_redacted(self, served) -> None:
        snapshot = McpServerConfigSnapshot(
            mcp_server_id="registered",
            mode="subprocess",
            command=["python", "-m", "srv"],
            env={"API_TOKEN": SECRET, "PLAIN_SETTING": SHAPED, "DB_PASSWORD": LITERAL, "REGION": "eu"},
        )
        served.ctx.runtime = Mock()
        served.ctx.runtime.config_repository = Mock(get_all=AsyncMock(return_value=[snapshot]))
        client = served()

        response = client.get("/config/")

        assert response.status_code == 200, response.text
        _no_secret(response.text)
        record = response.json()["config"]["mcp_servers"][0]
        assert record["mcp_server_id"] == "registered"
        assert record["command"] == ["python", "-m", "srv"]
        assert record["env"]["REGION"] == "eu"


class TestDiff:
    def _file(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("AUDIT_DIFF_SECRET", SECRET)
        path = tmp_path / "running.yaml"
        path.write_text(
            "mcp_servers:\n"
            "  srv:\n"
            "    mode: subprocess\n"
            "    command: [python, -m, srv]\n"
            "    env:\n"
            "      API_TOKEN: ${AUDIT_DIFF_SECRET}\n"
            f"      DB_PASSWORD: {LITERAL}\n"
            f"      PLAIN_SETTING: {SHAPED}\n"
            "      REGION: eu\n"
        )
        set_running_config_path(str(path))

    def test_no_secret_in_the_on_disk_side_or_the_diff(self, served, tmp_path, monkeypatch) -> None:
        self._file(tmp_path, monkeypatch)
        client = served({"mcp_servers": {}})

        response = client.get("/config/diff")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["has_diff"] is True
        _no_secret(response.text)
        assert "REGION: eu" in body["diff"]
        assert body["on_disk"]["mcp_servers"]["srv"]["env"]["REGION"] == "eu"

    def test_an_unchanged_file_still_reports_no_diff(self, served, tmp_path, monkeypatch) -> None:
        # The in-memory side is what export serves, already redacted. The same
        # rule on the file's side keeps the two comparable.
        self._file(tmp_path, monkeypatch)
        in_memory = {
            "mcp_servers": {
                "srv": {
                    "mode": "subprocess",
                    "command": ["python", "-m", "srv"],
                    "env": {
                        "API_TOKEN": "[REDACTED]",
                        "DB_PASSWORD": "[REDACTED]",
                        "PLAIN_SETTING": SHAPED,
                        "REGION": "eu",
                    },
                }
            }
        }
        client = served(in_memory)

        body = client.get("/config/diff").json()

        assert body["has_diff"] is False, body["diff"]
        assert body["diff"] == ""
