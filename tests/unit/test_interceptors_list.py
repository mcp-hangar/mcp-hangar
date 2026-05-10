"""Unit tests for interceptors/list endpoint."""

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from mcp_hangar.fastmcp_server.interceptors_list import (
    interceptors_list_handler,
    interceptors_list_response,
)


class TestInterceptorsListResponse:

    def test_response_shape(self):
        data = interceptors_list_response()
        assert "interceptors" in data
        assert len(data["interceptors"]) == 1

    def test_interceptor_fields(self):
        interceptor = interceptors_list_response()["interceptors"][0]
        assert interceptor["name"] == "mcp-hangar"
        assert isinstance(interceptor["version"], str)
        assert interceptor["type"] == "validator"
        assert interceptor["supportedEvents"] == ["tools/call", "tools/list"]
        assert interceptor["modes"] == ["audit", "enforce"]
        assert interceptor["trustBoundary"] == "host"

    def test_version_is_nonempty(self):
        interceptor = interceptors_list_response()["interceptors"][0]
        assert len(interceptor["version"]) > 0


class TestInterceptorsListEndpoint:

    @pytest.fixture()
    def client(self) -> TestClient:
        app = Starlette(routes=[
            Route("/interceptors/list", interceptors_list_handler, methods=["GET"]),
        ])
        return TestClient(app)

    def test_get_returns_200(self, client: TestClient):
        resp = client.get("/interceptors/list")
        assert resp.status_code == 200

    def test_get_returns_json(self, client: TestClient):
        resp = client.get("/interceptors/list")
        assert resp.headers["content-type"] == "application/json"
        data = resp.json()
        assert "interceptors" in data

    def test_post_not_allowed(self, client: TestClient):
        resp = client.post("/interceptors/list")
        assert resp.status_code == 405
