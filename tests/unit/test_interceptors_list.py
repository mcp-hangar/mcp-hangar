"""Unit tests for interceptors/list endpoint."""

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Route

from mcp.server.fastmcp import FastMCP

from mcp_hangar.fastmcp_server.interceptors_list import (
    interceptors_list_handler,
    interceptors_list_response,
    register_interceptors_list,
)


class TestInterceptorsListResponse:
    def test_response_shape(self):
        data = interceptors_list_response()
        assert "interceptors" in data
        assert len(data["interceptors"]) == 2

    def test_interceptor_fields(self):
        interceptor = interceptors_list_response()["interceptors"][0]
        assert interceptor["name"] == "mcp-hangar-validator"
        assert isinstance(interceptor["version"], str)
        assert interceptor["type"] == "validator"
        assert interceptor["supportedEvents"] == ["tools/call", "tools/list"]
        assert interceptor["modes"] == ["audit", "enforce"]
        assert interceptor["trustBoundary"] == "host"

    def test_mutator_entry(self):
        mutator = interceptors_list_response()["interceptors"][1]
        assert mutator["name"] == "mcp-hangar-mutator"
        assert mutator["type"] == "mutator"
        assert mutator["supportedEvents"] == ["tools/call"]
        assert mutator["modes"] == ["enforce"]
        assert mutator["trustBoundary"] == "host"

    def test_version_is_nonempty(self):
        interceptor = interceptors_list_response()["interceptors"][0]
        assert len(interceptor["version"]) > 0

    def test_interceptor_names_are_unique(self):
        data = interceptors_list_response()
        names = [i["name"] for i in data["interceptors"]]
        assert len(names) == len(set(names))


class TestInterceptorsListEndpoint:
    @pytest.fixture()
    def client(self) -> TestClient:
        app = Starlette(
            routes=[
                Route("/interceptors/list", interceptors_list_handler, methods=["GET"]),
            ]
        )
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


class TestRegisterInterceptorsList:
    def test_register_adds_two_custom_routes(self):
        mcp = FastMCP("test-interceptors")
        before = len(mcp._custom_starlette_routes)
        register_interceptors_list(mcp)
        # interceptors/list + interceptor/invoke (PR #2624).
        assert len(mcp._custom_starlette_routes) == before + 2

    def test_registered_route_path(self):
        mcp = FastMCP("test-interceptors")
        register_interceptors_list(mcp)
        paths = {r.path for r in mcp._custom_starlette_routes}
        assert "/interceptors/list" in paths
        assert "/interceptor/invoke" in paths

    def test_registered_route_serves_200(self):
        mcp = FastMCP("test-interceptors")
        register_interceptors_list(mcp)
        app = mcp.streamable_http_app()
        client = TestClient(app)
        resp = client.get("/interceptors/list")
        assert resp.status_code == 200
        assert "interceptors" in resp.json()
