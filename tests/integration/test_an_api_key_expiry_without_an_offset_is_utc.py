"""An API key whose `expires_at` has no UTC offset works until it expires (#2046).

It was accepted (201) and then every authentication with it answered 500: the
store compared the offset-naive expiry with an aware "now". An unparseable
`expires_at` answered 500 at creation.

The served stack, as ``serve --http`` builds it: the real SQLite key store,
roles from ``bootstrap_auth``, the real authorizer, ``create_api_router`` inside
``create_auth_enforced_app``.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.auth.bootstrap import bootstrap_auth
from mcp_hangar.auth.commands.handlers import register_auth_command_handlers
from mcp_hangar.auth.config import ApiKeyAuthConfig, AuthConfig, RoleAssignment, StorageConfig
from mcp_hangar.bootstrap.runtime import create_runtime
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.query_bus import QueryBus
from mcp_hangar.server.api import create_api_router
from mcp_hangar.server.api.middleware import create_auth_enforced_app
from mcp_hangar.server.context import init_context, reset_context


@pytest.fixture
def served(tmp_path: Path):
    reset_context()
    db = tmp_path / "auth.db"
    config = AuthConfig(
        enabled=True,
        allow_anonymous=False,
        api_key=ApiKeyAuthConfig(enabled=True),
        storage=StorageConfig(driver="sqlite", path=str(db)),
        role_assignments=[RoleAssignment(principal="svc:admin", role="admin", scope="global")],
    )
    components = bootstrap_auth(config)
    admin = components.api_key_store.create_key(principal_id="svc:admin", name="admin")
    event_store = InMemoryEventStore()
    runtime = create_runtime(
        event_bus=EventBus(event_store=event_store), command_bus=CommandBus(), query_bus=QueryBus()
    )
    register_auth_command_handlers(
        runtime.command_bus, api_key_store=components.api_key_store, role_store=components.role_store
    )
    ctx = init_context(runtime)
    ctx.auth_components = components
    app = create_auth_enforced_app(
        Starlette(routes=[Mount("/api", app=create_api_router(auth_components=components))]), components
    )
    yield TestClient(app), admin, db
    reset_context()


def _create(client: TestClient, admin: str, name: str, expires_at: str):
    return client.post(
        "/api/auth/keys",
        json={"principal_id": f"svc:{name}", "name": name, "expires_at": expires_at},
        headers={"X-API-Key": admin},
    )


def _me(client: TestClient, key: str) -> int:
    return client.get("/api/system/me", headers={"X-API-Key": key}).status_code


def test_a_future_expiry_without_an_offset_authenticates(served) -> None:
    client, admin, _ = served
    later = (datetime.now(UTC) + timedelta(days=30)).replace(tzinfo=None).isoformat()

    created = _create(client, admin, "later", later)

    assert created.status_code == 201, created.text
    assert _me(client, created.json()["raw_key"]) == 200


def test_a_past_expiry_without_an_offset_is_expired_not_a_500(served) -> None:
    client, admin, _ = served
    earlier = (datetime.now(UTC) - timedelta(days=1)).replace(tzinfo=None).isoformat()

    created = _create(client, admin, "earlier", earlier)

    assert created.status_code == 201, created.text
    reply = client.get("/api/system/me", headers={"X-API-Key": created.json()["raw_key"]})
    assert reply.status_code == 401
    assert "expired" in reply.text


def test_an_unparseable_expiry_is_a_422(served) -> None:
    client, admin, _ = served

    reply = _create(client, admin, "odd", "tomorrow")

    assert reply.status_code == 422, reply.text
    assert "expires_at" in reply.text


def test_a_key_stored_without_an_offset_before_the_fix_authenticates(served) -> None:
    client, admin, db = served
    created = _create(client, admin, "legacy", (datetime.now(UTC) + timedelta(days=30)).isoformat())
    assert created.status_code == 201, created.text
    naive = (datetime.now(UTC) + timedelta(days=30)).replace(tzinfo=None).isoformat()
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE api_keys SET expires_at = ? WHERE name = 'legacy'", (naive,))

    assert _me(client, created.json()["raw_key"]) == 200
