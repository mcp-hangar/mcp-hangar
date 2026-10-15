"""`GET /api/auth/principals` lists the principals that hold a role, on every store (#1672).

The handler probed the store for `list_principals` or a private `_assignments`
and answered `[]` when it found neither, which was every store but the in-memory
one: SQLite, Postgres and the event-sourced store. `list_principals` is now part
of the store contract and the handler calls it.

The same assertions run against each store that needs no server: in-memory,
SQLite on a temporary file, and the event-sourced store on the in-memory event
store. Postgres has the same query as SQLite and is covered by
`test_postgres_auth_store.py` where a database is available.
"""

from __future__ import annotations

import pytest

from mcp_hangar.auth.infrastructure.event_sourced_store import EventSourcedRoleStore
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore
from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteRoleStore
from mcp_hangar.auth.queries.handlers import ListPrincipalsHandler
from mcp_hangar.auth.queries.queries import ListPrincipalsQuery
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


@pytest.fixture(params=["memory", "sqlite", "event_sourced"])
def store(request, tmp_path):
    if request.param == "memory":
        yield InMemoryRoleStore()
    elif request.param == "sqlite":
        s = SQLiteRoleStore(db_path=str(tmp_path / "auth.db"))
        s.initialize()
        try:
            yield s
        finally:
            s.close()
    else:
        yield EventSourcedRoleStore(event_store=InMemoryEventStore())


def test_an_empty_store_lists_nobody(store):
    assert store.list_principals() == []


def test_principals_are_listed_with_their_roles_across_scopes(store):
    store.assign_role("service:operator", "admin")
    store.assign_role("user:ann", "viewer")
    store.assign_role("user:ann", "developer", scope="tenant:a")

    assert store.list_principals() == [
        {"principal_id": "service:operator", "roles": ["admin"]},
        {"principal_id": "user:ann", "roles": ["developer", "viewer"]},
    ]


def test_a_principal_whose_assignment_was_revoked_is_not_listed(store):
    store.assign_role("user:bob", "viewer")
    store.revoke_role("user:bob", "viewer")

    assert store.list_principals() == []


def test_the_query_handler_answers_what_the_store_lists(store):
    store.assign_role("user:ann", "viewer")

    answer = ListPrincipalsHandler(store).handle(ListPrincipalsQuery())

    assert answer == {"principals": [{"principal_id": "user:ann", "roles": ["viewer"]}], "total": 1}
