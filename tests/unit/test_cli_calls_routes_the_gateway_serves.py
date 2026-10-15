"""`status`, `add` and `remove` call routes the gateway serves (#1659).

They posted to `/reload` and `/MCP servers/{name}/stop` and read `/health`:
none exists, every call answered 404, and each command went on as if no gateway
ran. The calls here go through the real route table: a fake gateway answers only
the paths `server/api/router.py` mounts, and anything else is a 404.
"""

from __future__ import annotations

import json

import httpx
import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli import app
from mcp_hangar.server.cli.services import gateway_api

runner = CliRunner()


class FakeGateway:
    """Answers the paths the REST API mounts; records what it was asked."""

    def __init__(self, *, require_key: str | None = None, servers: list[dict] | None = None):
        self.require_key = require_key
        self.servers = servers if servers is not None else []
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, **kwargs) -> httpx.Response:
        path = httpx.URL(url).path
        self.calls.append((method, path))
        request = httpx.Request(method, url)
        if self.require_key and kwargs.get("headers", {}).get("X-API-Key") != self.require_key:
            return httpx.Response(401, json={"error": "unauthorized"}, request=request)
        if (method, path) == ("GET", "/api/mcp_servers/"):
            return httpx.Response(200, json={"mcp_servers": self.servers}, request=request)
        if (method, path) == ("POST", "/api/config/reload"):
            return httpx.Response(200, json={"status": "reloaded"}, request=request)
        if method == "POST" and path.startswith("/api/mcp_servers/") and path.endswith("/stop"):
            return httpx.Response(200, json={"stopped": True}, request=request)
        return httpx.Response(404, json={"error": "not found"}, request=request)


@pytest.fixture
def env(monkeypatch):
    for name in ("MCP_HANGAR_URL", "MCP_HANGAR_API_KEY", "MCP_HTTP_HOST", "MCP_HTTP_PORT"):
        monkeypatch.delenv(name, raising=False)


def _no_gateway(*_a, **_k):
    raise httpx.ConnectError("refused")


class TestTheHelper:
    def test_it_asks_the_configured_host_and_port(self, env, monkeypatch):
        monkeypatch.setenv("MCP_HTTP_HOST", "0.0.0.0")
        monkeypatch.setenv("MCP_HTTP_PORT", "9100")
        assert gateway_api.candidate_urls() == ["http://127.0.0.1:9100"]

    def test_an_explicit_url_wins(self, env, monkeypatch):
        monkeypatch.setenv("MCP_HANGAR_URL", "https://hangar.example/")
        assert gateway_api.candidate_urls() == ["https://hangar.example"]

    def test_it_sends_the_api_key(self, env, monkeypatch):
        gateway = FakeGateway(require_key="k1")
        monkeypatch.setattr(gateway_api.httpx, "request", gateway)
        monkeypatch.setenv("MCP_HANGAR_API_KEY", "k1")
        assert gateway_api.call("GET", "/api/mcp_servers/").ok

    def test_a_refusal_names_the_key_variable(self, env, monkeypatch):
        monkeypatch.setattr(gateway_api.httpx, "request", FakeGateway(require_key="k1"))
        reply = gateway_api.call("GET", "/api/mcp_servers/")
        assert reply.refused_for_auth and "MCP_HANGAR_API_KEY" in (reply.problem() or "")

    def test_no_gateway_is_not_a_problem(self, env, monkeypatch):
        monkeypatch.setattr(gateway_api.httpx, "request", _no_gateway)
        reply = gateway_api.call("GET", "/api/mcp_servers/")
        assert not reply.reached and reply.problem() is None


class TestStatus:
    def test_it_reflects_a_running_gateway(self, env, monkeypatch):
        servers = [
            {
                "mcp_server_id": "math",
                "state": "ready",
                "mode": "subprocess",
                "tools_count": 3,
                "health_status": "healthy",
            }
        ]
        monkeypatch.setattr(gateway_api.httpx, "request", FakeGateway(servers=servers))
        result = runner.invoke(app, ["--json", "status"])
        data = json.loads(result.stdout)
        assert data["server_running"] is True
        assert data["mcp_servers"][0]["name"] == "math"
        assert data["mcp_servers"][0]["state"] == "READY"
        assert data["mcp_servers"][0]["tools_count"] == 3

    def test_a_refused_call_says_so_on_stderr_and_stdout_stays_json(self, env, monkeypatch, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("mcp_servers:\n  math:\n    mode: subprocess\n    command: [x]\n")
        monkeypatch.setattr(gateway_api.httpx, "request", FakeGateway(require_key="k1"))
        result = runner.invoke(app, ["--config", str(config), "--json", "status"], catch_exceptions=False)
        assert '"server_running": false' in result.stdout
        assert "MCP_HANGAR_API_KEY" in result.output


class TestAddAndRemove:
    def test_remove_stops_the_server_on_the_real_route(self, env, monkeypatch, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("mcp_servers:\n  math:\n    mode: subprocess\n    command: [x]\n")
        gateway = FakeGateway()
        monkeypatch.setattr(gateway_api.httpx, "request", gateway)
        result = runner.invoke(app, ["--config", str(config), "remove", "math", "-y"], catch_exceptions=False)
        assert ("POST", "/api/mcp_servers/math/stop") in gateway.calls
        assert "Stopped running instance" in result.output

    def test_remove_reports_a_refused_stop(self, env, monkeypatch, tmp_path):
        config = tmp_path / "config.yaml"
        config.write_text("mcp_servers:\n  math:\n    mode: subprocess\n    command: [x]\n")
        monkeypatch.setattr(gateway_api.httpx, "request", FakeGateway(require_key="k1"))
        result = runner.invoke(app, ["--config", str(config), "remove", "math", "-y"], catch_exceptions=False)
        assert "Could not stop math" in result.output and "MCP_HANGAR_API_KEY" in result.output

    def test_add_reloads_through_the_real_route(self, env, monkeypatch):
        from mcp_hangar.server.cli.commands import add

        gateway = FakeGateway()
        monkeypatch.setattr(gateway_api.httpx, "request", gateway)
        assert add._try_hot_reload().ok
        assert ("POST", "/api/config/reload") in gateway.calls


class TestTheRoutesExist:
    """The fake above answers what we tell it to; the route table is the authority."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/mcp_servers/"),
            ("POST", "/config/reload"),
            ("POST", "/mcp_servers/math/stop"),
        ],
    )
    def test_the_gateway_serves_the_path_the_cli_calls(self, method, path):
        from starlette.routing import Match

        from mcp_hangar.server.api.router import create_api_router

        scope = {"type": "http", "method": method, "path": path, "root_path": ""}
        assert any(route.matches(scope)[0] == Match.FULL for route in create_api_router().routes), (method, path)
