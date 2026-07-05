"""Unit tests for tool:invoke authorization on the hangar_call path.

The MCP ``hangar_call`` tool-invoke path must enforce the same RBAC as the REST
API (RBAC #386 originally covered only REST, so any caller could invoke tools
regardless of role). These tests assert the fail-closed semantics of the
authorization gate added to ``hangar_call``:

- authz middleware not configured (stdio / no-auth) -> ALLOW (backward compat).
- auth configured but principal missing/anonymous -> DENY.
- principal lacking ``tool:invoke`` -> DENY (per call).
- principal with ``tool:invoke`` -> ALLOW (reaches the executor).
- mixed batch -> only the unauthorized tools are denied; the rest execute.

The tests mock ``get_context``/authz and the batch executor -- they deliberately
do NOT call ``bootstrap()`` (which registers process-global command handlers and
clashes across the suite).
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import mcp_hangar.server.tools.batch as batch_mod
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.models import BatchResult, CallResult


def _make_ctx(principal):
    """Build a fake FastMCP Context exposing request.state.auth.principal."""
    ctx = MagicMock()
    ctx.request_context.request.state.auth.principal = principal
    return ctx


def _make_principal(*, anonymous=False):
    principal = MagicMock()
    principal.is_anonymous.return_value = anonymous
    return principal


def _make_executor():
    """An executor stub that reports every submitted call as a success."""
    executor = MagicMock()

    def _execute(*, batch_id, calls, **_kwargs):
        results = [
            CallResult(
                index=spec.index,
                call_id=spec.call_id,
                success=True,
                result={"ok": True},
                elapsed_ms=1.0,
            )
            for spec in calls
        ]
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=1.0,
            results=results,
        )

    executor.execute.side_effect = _execute
    return executor


@contextmanager
def _patched(authz_middleware, executor):
    """Patch validation, identity bridging, app context and the executor.

    - validate_batch -> always valid (no real mcp_server registry needed).
    - get_identity_context -> non-None so the identity-bridging block is skipped.
    - get_context -> a fake app context whose auth_components.authz_middleware is
      the supplied middleware (or None for the no-auth case).
    - _executor -> the supplied stub.
    """
    app_ctx = MagicMock()
    app_ctx.auth_components.authz_middleware = authz_middleware
    with (
        patch.object(batch_mod, "validate_batch", return_value=[]),
        patch.object(batch_mod, "get_identity_context", return_value=object()),
        patch.object(batch_mod, "get_context", return_value=app_ctx),
        patch.object(batch_mod, "_executor", executor),
    ):
        yield


def _call(tool="do_thing"):
    return {"mcp_server": "svc", "tool": tool, "arguments": {}}


def test_no_auth_configured_allows() -> None:
    """authz_middleware is None (stdio/local) -> the call executes."""
    executor = _make_executor()
    with _patched(authz_middleware=None, executor=executor):
        result = hangar_call(calls=[_call()], ctx=_make_ctx(_make_principal()))

    assert result["success"] is True
    assert result["succeeded"] == 1
    assert result["results"][0]["success"] is True
    executor.execute.assert_called_once()


def test_missing_principal_denied() -> None:
    """auth configured but no principal on the request -> denied, not executed."""
    authz = MagicMock()
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = hangar_call(calls=[_call()], ctx=_make_ctx(None))

    assert result["success"] is False
    assert result["failed"] == 1
    assert result["results"][0]["success"] is False
    assert result["results"][0]["error_type"] == "AuthorizationDenied"
    executor.execute.assert_not_called()
    authz.authorize.assert_not_called()


def test_anonymous_principal_denied() -> None:
    """An anonymous principal under configured auth -> denied."""
    authz = MagicMock()
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = hangar_call(calls=[_call()], ctx=_make_ctx(_make_principal(anonymous=True)))

    assert result["success"] is False
    assert result["results"][0]["error_type"] == "AuthorizationDenied"
    executor.execute.assert_not_called()


def test_principal_lacking_tool_invoke_denied() -> None:
    """authz.authorize raises (viewer lacks tool:invoke) -> denied fail-closed."""
    authz = MagicMock()
    authz.authorize.side_effect = AccessDeniedError(principal_id="view123", action="invoke", resource="tool")
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = hangar_call(calls=[_call("dangerous")], ctx=_make_ctx(_make_principal()))

    assert result["success"] is False
    assert result["failed"] == 1
    assert result["results"][0]["success"] is False
    assert result["results"][0]["error_type"] == "AuthorizationDenied"
    executor.execute.assert_not_called()
    # authorize was consulted with the tool as the resource id.
    _, kwargs = authz.authorize.call_args
    assert kwargs["action"] == "invoke"
    assert kwargs["resource_type"] == "tool"
    assert kwargs["resource_id"] == "dangerous"


def test_principal_with_tool_invoke_allowed() -> None:
    """authz.authorize returns (developer has tool:invoke) -> call executes."""
    authz = MagicMock()
    authz.authorize.return_value = None
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = hangar_call(calls=[_call()], ctx=_make_ctx(_make_principal()))

    assert result["success"] is True
    assert result["succeeded"] == 1
    assert result["results"][0]["success"] is True
    executor.execute.assert_called_once()


def test_mixed_batch_denies_only_unauthorized_tool() -> None:
    """Per-call gate: unauthorized tool denied, authorized tool still executes."""
    authz = MagicMock()

    def _authorize(*, principal, action, resource_type, resource_id, context=None):
        if resource_id == "danger":
            raise AccessDeniedError(principal_id="u", action=action, resource=resource_id)

    authz.authorize.side_effect = _authorize
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = hangar_call(
            calls=[_call("safe"), _call("danger")],
            ctx=_make_ctx(_make_principal()),
        )

    assert result["total"] == 2
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    # Results are returned in original call order.
    by_index = {r["index"]: r for r in result["results"]}
    assert by_index[0]["success"] is True
    assert by_index[1]["success"] is False
    assert by_index[1]["error_type"] == "AuthorizationDenied"
    # Only the authorized call reached the executor.
    _, kwargs = executor.execute.call_args
    assert len(kwargs["calls"]) == 1
    assert kwargs["calls"][0].tool == "safe"
