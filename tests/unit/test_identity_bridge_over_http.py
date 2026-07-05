"""Regression tests for the caller-identity bridge over streamable-HTTP.

Root cause (#384): FastMCP's streamable-HTTP transport runs tool calls in a
per-session task decoupled from the ASGI auth wrapper coroutine that sets
``identity_context_var``. As a result the batch executor read a ``None`` tenant
for EVERY authenticated HTTP caller, silently bypassing per-tenant enforcement
(canary routing, per-tenant tool withdrawal).

The fix gives ``hangar_call`` the FastMCP-injected request context and, when
``identity_context_var`` is unset, bridges the principal stored on the request
(``request.state.auth.principal``) into ``identity_context_var`` -- the same
contextvar the executor snapshots into its worker threads.

These tests drive ``hangar_call`` with a fake FastMCP context and assert the
executor path observes the bridged tenant, while stdio / no-request / unauth
paths leave identity as ``None`` (no crash, existing fallback unchanged).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from mcp.server.fastmcp import Context

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.models import BatchResult


def _fake_ctx_with_principal(principal: Any) -> Context:
    """Build a fake FastMCP Context exposing request.state.auth.principal.

    Mirrors the attribute chain the auth middleware populates
    (``_store_auth_context`` -> Starlette ``request.state.auth``) and that
    FastMCP exposes via ``ctx.request_context.request``.
    """
    state = SimpleNamespace(auth=SimpleNamespace(principal=principal))
    request = SimpleNamespace(state=state)
    request_context = SimpleNamespace(request=request)
    return cast(Context, SimpleNamespace(request_context=request_context))


@pytest.fixture
def _reset_identity():
    """Ensure identity_context_var starts and ends unset for isolation."""
    token = identity_context_var.set(None)
    try:
        yield
    finally:
        identity_context_var.reset(token)


@pytest.fixture
def _spy_executor(monkeypatch):
    """Replace the global executor's execute() with a spy that captures the
    identity the executor would observe at call time."""
    captured: dict[str, Any] = {}

    def _fake_execute(
        *, batch_id: str, calls, max_concurrency, global_timeout, fail_fast, request_ctx=None
    ) -> BatchResult:
        ident = get_identity_context()
        captured["identity"] = ident
        captured["tenant"] = ident.caller.tenant_id if ident is not None else None
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=0.0,
            results=[],
        )

    monkeypatch.setattr(batch._executor, "execute", _fake_execute)
    # Bypass registry-backed validation so the call reaches the executor spy;
    # this test targets the identity bridge, not batch validation.
    monkeypatch.setattr(batch, "validate_batch", lambda *a, **k: [])
    return captured


_ONE_CALL = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]


class TestIdentityBridgeOverHttp:
    def test_authenticated_http_caller_bridges_tenant_to_executor(self, _reset_identity, _spy_executor):
        """An authenticated principal on the request (identity_context_var unset)
        must reach the executor with the principal's tenant."""
        principal = Principal(
            id=PrincipalId("user:alice"),
            type=PrincipalType.USER,
            tenant_id="tenant-abc",
        )
        ctx = _fake_ctx_with_principal(principal)

        assert get_identity_context() is None  # precondition: nothing bound yet

        result = hangar_call(calls=list(_ONE_CALL), ctx=ctx)

        assert result["success"] is True
        # The executor saw the bridged identity with the principal's tenant.
        assert _spy_executor["tenant"] == "tenant-abc"
        assert _spy_executor["identity"] is not None
        assert _spy_executor["identity"].caller.principal_type == "user"
        assert _spy_executor["identity"].caller.user_id == "user:alice"

    def test_token_reset_after_call_no_leak(self, _reset_identity, _spy_executor):
        """The bridged identity must not leak past the call (reused session task)."""
        principal = Principal(
            id=PrincipalId("svc-ci"),
            type=PrincipalType.SERVICE_ACCOUNT,
            tenant_id="tenant-xyz",
        )
        ctx = _fake_ctx_with_principal(principal)

        hangar_call(calls=list(_ONE_CALL), ctx=ctx)

        assert _spy_executor["tenant"] == "tenant-xyz"
        # After the call, identity is back to the fixture's None (no leak).
        assert get_identity_context() is None

    def test_existing_identity_is_not_overridden(self, _spy_executor):
        """When identity_context_var is already bound (ASGI wrapper propagated it),
        the bridge must NOT override it."""
        from mcp_hangar.fastmcp_server.asgi import _principal_to_identity_context

        preexisting = _principal_to_identity_context(
            Principal(id=PrincipalId("user:bound"), type=PrincipalType.USER, tenant_id="tenant-preset")
        )
        token = identity_context_var.set(preexisting)
        try:
            # A DIFFERENT principal on the request must be ignored.
            other = Principal(id=PrincipalId("user:other"), type=PrincipalType.USER, tenant_id="tenant-other")
            ctx = _fake_ctx_with_principal(other)
            hangar_call(calls=list(_ONE_CALL), ctx=ctx)
            assert _spy_executor["tenant"] == "tenant-preset"
        finally:
            identity_context_var.reset(token)

    def test_no_ctx_stdio_path_identity_stays_none(self, _reset_identity, _spy_executor):
        """Direct/stdio invocation with no ctx must leave identity None (no crash)."""
        assert get_identity_context() is None
        result = hangar_call(calls=list(_ONE_CALL))
        assert result["success"] is True
        assert _spy_executor["tenant"] is None
        assert _spy_executor["identity"] is None
        assert get_identity_context() is None

    def test_ctx_without_request_leaves_identity_none(self, _reset_identity, _spy_executor):
        """A ctx whose request_context has no request (e.g. non-HTTP transport)
        must not crash and must leave identity None."""
        ctx = cast(Context, SimpleNamespace(request_context=SimpleNamespace(request=None)))
        result = hangar_call(calls=list(_ONE_CALL), ctx=ctx)
        assert result["success"] is True
        assert _spy_executor["tenant"] is None
        assert get_identity_context() is None

    def test_request_without_principal_leaves_identity_none(self, _reset_identity, _spy_executor):
        """A request with no auth principal (unauthenticated) must leave identity None."""
        request = SimpleNamespace(state=SimpleNamespace())  # no .auth attribute
        ctx = cast(Context, SimpleNamespace(request_context=SimpleNamespace(request=request)))
        result = hangar_call(calls=list(_ONE_CALL), ctx=ctx)
        assert result["success"] is True
        assert _spy_executor["tenant"] is None
        assert get_identity_context() is None

    def test_ctx_request_context_raises_is_fault_barriered(self, _reset_identity, _spy_executor):
        """If ctx.request_context raises (FastMCP raises ValueError outside a
        request), the bridge must swallow it and leave identity None."""

        class _RaisingCtx:
            @property
            def request_context(self):
                raise ValueError("Context is not available outside of a request")

        result = hangar_call(calls=list(_ONE_CALL), ctx=cast(Context, _RaisingCtx()))
        assert result["success"] is True
        assert _spy_executor["tenant"] is None
        assert get_identity_context() is None
