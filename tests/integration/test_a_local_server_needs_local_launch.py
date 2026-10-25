"""Registering a server the gateway launches on its host needs `mcp_servers:local_launch` (#2025).

A `subprocess`, `docker` or `container` server runs a command or image the
caller chooses on the gateway's own host, and its `env` is read when that
process starts. `mcp_servers:write` alone used to be enough for both, and the
built-in `developer` role holds it, so a developer key could run what it liked
on the gateway host. A `remote` server is a connection, not a process, and
stays on `mcp_servers:write`.

The stack is the one ``serve --http`` builds: API keys minted in the real
SQLite key store, roles assigned by ``bootstrap_auth``, the real
``RBACAuthorizer``, ``create_api_router`` mounted at ``/api`` inside
``create_auth_enforced_app``, and the real command handlers behind it. Nothing
in the auth path is mocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_handlers import register_crud_handlers
from mcp_hangar.application.queries import register_all_handlers as register_query_handlers
from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.auth.roles import BUILTIN_ROLES
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.context import init_context, reset_context

ROLES = ("admin", "provider-admin", "developer", "kubernetes-operator")
LOCAL = {
    "subprocess": {"command": ["python", "-m", "some_server"]},
    "docker": {"image": "example/server:1"},
    "container": {"image": "example/server:1"},
}
REMOTE = {"mode": "remote", "endpoint": "https://203.0.113.5:8443/mcp"}


def _app(tmp_path: Path, *, enabled: bool = True):
    config = AuthConfig(
        enabled=enabled,
        allow_anonymous=False,
        api_key=ApiKeyAuthConfig(enabled=True),
        storage=StorageConfig(driver="sqlite", path=str(tmp_path / "auth.db")),
        role_assignments=[RoleAssignment(principal=f"svc:{role}", role=role, scope="global") for role in ROLES],
    )
    components = bootstrap_auth(config)
    keys = {}
    if enabled:
        keys = {role: components.api_key_store.create_key(principal_id=f"svc:{role}", name="k") for role in ROLES}
    event_store = InMemoryEventStore()
    runtime = create_runtime(
        event_bus=EventBus(event_store=event_store), command_bus=CommandBus(), query_bus=QueryBus()
    )
    register_query_handlers(runtime.query_bus, runtime.repository, event_store=event_store)
    register_crud_handlers(runtime.command_bus, runtime.repository, runtime.event_bus, {})
    ctx = init_context(runtime)
    ctx.auth_components = components
    served = Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))])
    app = create_auth_enforced_app(served, components) if enabled else served
    return TestClient(app), keys


@pytest.fixture
def served(tmp_path):
    reset_context()
    client, keys = _app(tmp_path)
    yield client, keys
    reset_context()


def _create(client: TestClient, key: str | None, name: str, mode: str, **fields) -> int:
    headers = {"X-API-Key": key} if key else {}
    body = {"mcp_server_id": name, "mode": mode, **fields}
    return client.post("/api/mcp_servers/", json=body, headers=headers).status_code


class TestTheRoles:
    def test_the_permission_is_held_where_the_fleet_is_managed(self) -> None:
        held = {name for name, role in BUILTIN_ROLES.items() if role.has_permission("mcp_servers", "local_launch")}

        assert held == {"admin", "provider-admin"}


class TestCreate:
    @pytest.mark.parametrize("mode", sorted(LOCAL))
    def test_a_developer_cannot_register_a_local_server(self, served, mode) -> None:
        client, keys = served

        assert _create(client, keys["developer"], f"dev-{mode}", mode, **LOCAL[mode]) == 403
        assert client.get(f"/api/mcp_servers/dev-{mode}", headers={"X-API-Key": keys["admin"]}).status_code == 404

    def test_an_unknown_mode_is_checked_as_a_local_one(self, served) -> None:
        client, keys = served

        assert _create(client, keys["developer"], "dev-odd", "Subprocess ", command=["python"]) == 403

    def test_a_developer_still_registers_a_remote_server(self, served) -> None:
        client, keys = served

        assert _create(client, keys["developer"], "dev-remote", **REMOTE) == 201

    def test_the_operator_role_registers_only_remote_servers(self, served) -> None:
        client, keys = served

        assert _create(client, keys["kubernetes-operator"], "op-remote", **REMOTE) == 201
        assert _create(client, keys["kubernetes-operator"], "op-local", "subprocess", **LOCAL["subprocess"]) == 403

    @pytest.mark.parametrize("role", ["admin", "provider-admin"])
    @pytest.mark.parametrize("mode", sorted(LOCAL))
    def test_the_fleet_managers_register_local_servers(self, served, role, mode) -> None:
        client, keys = served

        assert _create(client, keys[role], f"{role}-{mode}", mode, **LOCAL[mode]) == 201


class TestUpdate:
    def _registered(self, client: TestClient, keys: dict[str, str]) -> None:
        assert _create(client, keys["admin"], "local", "subprocess", **LOCAL["subprocess"]) == 201
        assert _create(client, keys["admin"], "far", **REMOTE) == 201

    def test_a_developer_cannot_change_a_local_servers_env(self, served) -> None:
        client, keys = served
        self._registered(client, keys)

        reply = client.patch(
            "/api/mcp_servers/local", json={"env": {"X": "1"}}, headers={"X-API-Key": keys["developer"]}
        )

        assert reply.status_code == 403
        shown = client.get("/api/mcp_servers/local", headers={"X-API-Key": keys["admin"]}).json()
        assert "X" not in (shown.get("env") or {})

    def test_a_developer_still_changes_the_other_fields(self, served) -> None:
        client, keys = served
        self._registered(client, keys)
        dev = {"X-API-Key": keys["developer"]}

        assert (
            client.patch(
                "/api/mcp_servers/local", json={"description": "d", "idle_ttl_s": 120}, headers=dev
            ).status_code
            == 200
        )
        assert client.patch("/api/mcp_servers/far", json={"env": {"X": "1"}}, headers=dev).status_code == 200

    def test_a_fleet_manager_changes_a_local_servers_env(self, served) -> None:
        client, keys = served
        self._registered(client, keys)

        reply = client.put(
            "/api/mcp_servers/local", json={"env": {"X": "1"}}, headers={"X-API-Key": keys["provider-admin"]}
        )

        assert reply.status_code == 200

    def test_an_unknown_server_still_answers_404(self, served) -> None:
        client, keys = served

        reply = client.patch(
            "/api/mcp_servers/nope", json={"env": {"X": "1"}}, headers={"X-API-Key": keys["developer"]}
        )

        assert reply.status_code == 404


class TestAuthOff:
    def test_every_caller_may_as_on_every_other_route(self, tmp_path) -> None:
        reset_context()
        try:
            client, _ = _app(tmp_path, enabled=False)

            assert _create(client, None, "local", "subprocess", **LOCAL["subprocess"]) == 201
            assert client.patch("/api/mcp_servers/local", json={"env": {"X": "1"}}).status_code == 200
        finally:
            reset_context()
