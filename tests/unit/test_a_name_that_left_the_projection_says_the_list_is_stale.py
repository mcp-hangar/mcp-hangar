"""A name that left this caller's projection says the list is stale (#1368).

The memory, its key and the front door's two handlers, driven with the real
projection registry and the real resolver. The served path end to end, over
the streamable-HTTP transport and the real auth layer, is in
``tests/integration/test_a_stale_name_is_told_to_list_again.py``.

Naming: neutral placeholders only (store, read_item, write_item, tenant:a).
"""

from __future__ import annotations

from collections.abc import Iterator
import threading
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from mcp_hangar._sdk_compat import METHOD_NOT_FOUND, McpError
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.session_suspension import VERIFIED_SESSION_ID_KEY
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects import ToolAccessPolicy
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import catalogue_warmup, flat_tool_projection, served_tool_names
from mcp_hangar.fastmcp_server.served_tool_names import (
    PROJECTION_CHANGED,
    ServedNames,
    projection_changed_error_data,
    served_key,
)

SERVER = "store"
TENANT_A = "tenant:a"
TENANT_B = "tenant:b"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch) -> Iterator[None]:
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()
    get_tool_access_resolver().set_topology_mode("front_door")
    monkeypatch.setattr(served_tool_names, "SERVED", ServedNames())
    monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: frozenset())
    yield
    reset_tool_projection_registry()
    reset_tool_access_resolver()
    catalogue_warmup.reset()


def _schema(name: str) -> ToolSchema:
    return ToolSchema(name=name, description=f"Does {name}", input_schema={"type": "object", "properties": {}})


def _catalogue(*names: str) -> None:
    get_tool_projection_registry().build_from_tools(SERVER, [_schema(n) for n in names])


def _ctx(tenant_id: str | None, *, method: str, user: str = "user:one", session: str | None = None) -> SimpleNamespace:
    """An SDK v2 request context: the principal the auth layer left, and the POST it came on.

    *session* is the ``sid`` of a verified token, recorded where an
    authenticator records it, so it reaches ``CallerIdentity.session_id`` the
    way it does in production.
    """
    principal = Principal(
        id=PrincipalId(user),
        type=PrincipalType.USER,
        tenant_id=tenant_id,
        metadata={VERIFIED_SESSION_ID_KEY: session} if session else None,
    )
    body = (
        b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"read_item","arguments":{"x":"1"}}}'
        if method == "tools/call"
        else b'{"jsonrpc":"2.0","method":"tools/list","params":{}}'
    )
    request = SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal)), _body=body)
    return SimpleNamespace(request=request)


def _handlers() -> dict[str, Any]:
    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
    return handlers


def _list(tenant_id: str | None, **ctx: Any) -> list[str]:
    result = anyio.run(_handlers()["tools/list"], _ctx(tenant_id, method="tools/list", **ctx), SimpleNamespace())
    return [tool.name for tool in result.tools]


def _call_error(tenant_id: str | None, name: str, **ctx: Any) -> Any:
    """Call *name* and return the JSON-RPC error it was refused with."""
    params = SimpleNamespace(name=name, arguments={})
    with pytest.raises(McpError) as refused:
        anyio.run(_handlers()["tools/call"], _ctx(tenant_id, method="tools/call", **ctx), params)
    return refused.value.error


class TestTheReason:
    def test_a_name_that_left_this_callers_listing_carries_the_reason(self) -> None:
        _catalogue("read_item", "write_item")
        assert _list(TENANT_A) == ["read_item", "write_item"]

        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)
        error = _call_error(TENANT_A, "write_item")

        assert error.code == METHOD_NOT_FOUND
        assert error.message == "Tool 'write_item' not found"
        assert error.data == {"reason": PROJECTION_CHANGED}

    def test_the_reason_is_a_constant_that_names_nothing(self) -> None:
        assert projection_changed_error_data() == {"reason": "projection_changed"}
        assert projection_changed_error_data() is not projection_changed_error_data(), (
            "a caller could mutate a shared one"
        )

    def test_a_name_this_caller_was_never_served_gets_the_ordinary_error(self) -> None:
        """Tenant B is denied write_item by policy; tenant A holds it. B cannot tell."""
        _catalogue("read_item", "write_item")
        get_tool_access_resolver().set_standalone_member_policy(
            SERVER, TENANT_B, ToolAccessPolicy(allow_list=("read_item",))
        )
        assert _list(TENANT_A) == ["read_item", "write_item"]
        assert _list(TENANT_B) == ["read_item"]

        denied = _call_error(TENANT_B, "write_item")
        _catalogue("read_item")  # now write_item exists nowhere
        absent = _call_error(TENANT_B, "write_item")

        assert denied.model_dump() == absent.model_dump()
        assert denied.data is None
        assert "data" not in denied.model_dump(exclude_unset=True)

    def test_a_fresh_listing_ends_the_reason(self) -> None:
        _catalogue("read_item", "write_item")
        _list(TENANT_A)
        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)
        assert _call_error(TENANT_A, "write_item").data is not None

        assert _list(TENANT_A) == ["read_item"]

        assert _call_error(TENANT_A, "write_item").data is None

    def test_an_empty_listing_clears_what_was_served(self) -> None:
        _catalogue("write_item")
        _list(TENANT_A)
        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

        assert _list(TENANT_A) == []

        assert len(served_tool_names.SERVED) == 0
        assert _call_error(TENANT_A, "write_item").data is None


class TestWhatCountsAsServed:
    def test_the_pre_dispatch_listing_on_a_call_is_not_remembered(self) -> None:
        """The SDK lists on a tools/call to resolve Mcp-Param schemas (#1049). The client received nothing."""
        _catalogue("read_item", "write_item")
        anyio.run(_handlers()["tools/list"], _ctx(TENANT_A, method="tools/call"), SimpleNamespace())
        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

        assert len(served_tool_names.SERVED) == 0
        assert _call_error(TENANT_A, "write_item").data is None

    def test_a_caller_without_a_tenant_is_never_remembered(self) -> None:
        _catalogue("read_item")
        assert _list(None) == []

        assert len(served_tool_names.SERVED) == 0
        assert _call_error(None, "read_item").data is None

    def test_another_principal_of_the_same_tenant_is_not_answered_from_this_one(self) -> None:
        _catalogue("read_item", "write_item")
        _list(TENANT_A, user="user:one")
        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

        assert _call_error(TENANT_A, "write_item", user="user:one").data is not None
        assert _call_error(TENANT_A, "write_item", user="user:two").data is None

    def test_another_session_of_the_same_principal_is_not_answered_from_this_one(self) -> None:
        _catalogue("read_item", "write_item")
        _list(TENANT_A, session="session-one")
        get_tool_projection_registry().withdraw(SERVER, "write_item", tenant_id=TENANT_A)

        assert _call_error(TENANT_A, "write_item", session="session-one").data is not None
        assert _call_error(TENANT_A, "write_item", session="session-two").data is None
        assert _call_error(TENANT_A, "write_item").data is None


def _key_for(caller: CallerIdentity) -> Any:
    token = identity_context_var.set(IdentityContext(caller=caller))
    try:
        return served_key()
    finally:
        identity_context_var.reset(token)


def _caller(**fields: Any) -> CallerIdentity:
    base: dict[str, Any] = {
        "user_id": "user:one",
        "agent_id": None,
        "session_id": None,
        "principal_type": "user",
        "tenant_id": TENANT_A,
    }
    return CallerIdentity(**{**base, **fields})


class TestTheKey:
    def test_no_identity_and_no_tenant_have_no_key(self) -> None:
        assert served_key() is None
        assert _key_for(_caller(tenant_id=None)) is None
        assert _key_for(_caller(tenant_id="")) is None

    def test_the_tenant_and_the_principal_are_always_in_the_key(self) -> None:
        mine = _key_for(_caller())

        assert _key_for(_caller(tenant_id=TENANT_B)) != mine
        assert _key_for(_caller(user_id="user:two")) != mine
        assert _key_for(_caller(principal_type="service")) != mine
        assert _key_for(_caller(agent_id="agent:one")) != mine
        assert _key_for(_caller()) == mine

    def test_a_session_narrows_the_key_and_never_replaces_the_identity(self) -> None:
        mine = _key_for(_caller(session_id="session-one"))

        assert mine != _key_for(_caller())
        assert mine != _key_for(_caller(session_id="session-two"))
        # The same session id under another tenant or principal is another key.
        assert mine != _key_for(_caller(session_id="session-one", tenant_id=TENANT_B))
        assert mine != _key_for(_caller(session_id="session-one", user_id="user:two"))


class TestTheMemoryIsBounded:
    def test_it_holds_at_most_its_bound_and_evicts_the_least_recently_used(self) -> None:
        served = ServedNames(max_identities=2)
        first, second, third = (("t", "user", f"u{i}", None, None) for i in range(3))
        served.remember(first, ["read_item"])
        served.remember(second, ["read_item"])
        assert served.was_served(first, "read_item")  # first is now the most recent

        served.remember(third, ["read_item"])

        assert len(served) == 2
        assert served.was_served(first, "read_item")
        assert not served.was_served(second, "read_item"), "the least recently used identity was kept"
        assert served.was_served(third, "read_item")

    def test_a_bound_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError):
            ServedNames(max_identities=0)

    def test_concurrent_listings_and_calls_keep_the_bound(self) -> None:
        served = ServedNames(max_identities=64)
        errors: list[BaseException] = []

        def worker(n: int) -> None:
            try:
                for i in range(500):
                    key = ("t", "user", f"u{(n * 500 + i) % 200}", None, None)
                    served.remember(key, [f"tool_{i % 7}"])
                    served.was_served(key, f"tool_{i % 7}")
            except BaseException as exc:  # noqa: BLE001 -- collected and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(served) == 64
