from starlette.datastructures import State
from starlette.requests import Request

from mcp_hangar.server.api.middleware import _store_auth_context


class _FakeAuth:
    def __init__(self, principal_id: str) -> None:
        self.principal_id = principal_id


def test_store_auth_context_creates_state_when_missing():
    scope: dict = {"type": "http"}
    ctx = _FakeAuth("alice")

    _store_auth_context(scope, ctx)

    assert isinstance(scope["state"], State)
    assert scope["state"].auth is ctx


def test_store_auth_context_preserves_existing_state():
    state = State()
    state.user_id = "preexisting"
    scope: dict = {"type": "http", "state": state}
    ctx = _FakeAuth("bob")

    _store_auth_context(scope, ctx)

    assert scope["state"] is state
    assert scope["state"].auth is ctx
    assert scope["state"].user_id == "preexisting"


def test_store_auth_context_replaces_legacy_dict_state():
    scope: dict = {"type": "http", "state": {"some_key": "value"}}
    ctx = _FakeAuth("carol")

    _store_auth_context(scope, ctx)

    assert isinstance(scope["state"], State)
    assert scope["state"].auth is ctx


def test_request_state_auth_readable_through_starlette():
    scope: dict = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
    }
    ctx = _FakeAuth("dave")

    _store_auth_context(scope, ctx)
    request = Request(scope)

    assert request.state.auth is ctx


def test_no_scope_auth_dead_write():
    scope: dict = {"type": "http"}
    ctx = _FakeAuth("eve")

    _store_auth_context(scope, ctx)

    assert "auth" not in scope
