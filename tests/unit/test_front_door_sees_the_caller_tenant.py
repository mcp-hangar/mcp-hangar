"""The front door's lowlevel handlers must see who is calling.

In `front_door` mode `_compute_effective_policy` denies everything when
`member_id is None` -- correctly, since a caller with no tenant must reach no
tool. So an unbound tenant does not fail loudly: it produces an empty
`tools/list`, indistinguishable from "no tools configured".

That is what shipped. On SDK v2 the streamable-HTTP transport runs each inbound
message in a per-session task, decoupled from the ASGI wrapper that sets
`identity_context_var`; the v2 adapters registered for `tools/list` and
`tools/call` were handed the SDK's per-request context -- which carries the HTTP
request, and therefore the authenticated principal -- and dropped it.

Measured on a live gateway before the fix: registry populated with 10 tools,
`AuthenticationSucceeded ... tenant_id: "tenant:a"` on the same pod, no member
policies configured at all, and `tools/list` returned 0 tools.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.fastmcp_server.asgi import bind_caller_identity, release_caller_identity


def _request_context_with(principal: Principal | None) -> SimpleNamespace:
    """A stand-in for the SDK's `ServerRequestContext`.

    Shape mirrors what the transport builds: a `request` whose `state.auth`
    carries what the auth middleware attached.
    """
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=auth)))


@pytest.fixture
def tenant_principal() -> Principal:
    return Principal(
        id=PrincipalId("user:alice"),
        type=PrincipalType.USER,
        tenant_id="tenant:a",
    )


class TestTheBridgeReadsAPerRequestContext:
    def test_it_binds_the_tenant_from_a_request_context(self, tenant_principal) -> None:
        from mcp_hangar.context import get_identity_context

        token = bind_caller_identity(_request_context_with(tenant_principal))
        try:
            identity = get_identity_context()
            assert identity is not None
            assert identity.caller.tenant_id == "tenant:a"
        finally:
            release_caller_identity(token)

    def test_it_also_accepts_the_high_level_context_shape(self, tenant_principal) -> None:
        # Tool bodies are handed a `Context` that wraps the request context.
        # One bridge serves both, so a fix on one path cannot miss the other --
        # which is exactly how the flat handlers were left out.
        from mcp_hangar.context import get_identity_context

        wrapper = SimpleNamespace(request_context=_request_context_with(tenant_principal))

        token = bind_caller_identity(wrapper)
        try:
            assert get_identity_context().caller.tenant_id == "tenant:a"
        finally:
            release_caller_identity(token)

    @pytest.mark.parametrize(
        "context",
        [None, SimpleNamespace(), _request_context_with(None)],
        ids=["none", "no-request", "unauthenticated"],
    )
    def test_it_changes_nothing_when_there_is_no_caller(self, context: Any) -> None:
        from mcp_hangar.context import get_identity_context

        token = bind_caller_identity(context)

        assert token is None
        assert get_identity_context() is None

    def test_the_binding_is_released(self, tenant_principal) -> None:
        # A per-session task serves many requests. A tenant left bound after one
        # would be read by the next -- one tenant's surface served to another.
        from mcp_hangar.context import get_identity_context

        token = bind_caller_identity(_request_context_with(tenant_principal))
        release_caller_identity(token)

        assert get_identity_context() is None


class TestTheRegisteredHandlersUseIt:
    """The adapters registered on the lowlevel server, not just the helper.

    The helper existed for tool bodies all along; the defect was that these two
    call sites never used it. Asserting the helper alone would have passed
    while front-door mode served nothing.
    """

    def _registered(self) -> dict[str, Any]:
        handlers: dict[str, Any] = {}

        class _Low:
            def add_request_handler(self, method, params_type, handler):
                handlers[method] = handler

        # No `list_tools` attribute -> the SDK v2 branch, which is the one that
        # ships and the one that dropped the context.
        flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
        return handlers

    @pytest.mark.parametrize("method", ["tools/list", "tools/call"])
    def test_the_handler_binds_identity_from_its_context(self, method, tenant_principal, monkeypatch) -> None:
        seen: list[str | None] = []
        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda tenant_id: (seen.append(tenant_id), {})[1])

        handler = self._registered()[method]
        params = SimpleNamespace(name="add", arguments={})

        import anyio

        async def _drive() -> None:
            # `tools/list` returns an empty result and `tools/call` raises
            # METHOD_NOT_FOUND on an empty map; neither outcome is what is
            # under test here, only which tenant the map was built for.
            try:
                await handler(_request_context_with(tenant_principal), params)
            except Exception:  # noqa: BLE001
                pass

        anyio.run(_drive)

        assert seen, f"{method} never built a flat map"
        assert seen[0] == "tenant:a", (
            f"{method} built its map for {seen[0]!r}; the caller's tenant was on the context it was handed"
        )
