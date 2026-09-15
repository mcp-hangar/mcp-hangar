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

The facade's ``invoke`` takes the same gate through ``call_as`` (#1453), for a
caller the embedder names instead of one on a request.

The tests mock ``get_context``/authz and the batch executor -- they deliberately
do NOT call ``bootstrap()`` (which registers process-global command handlers and
clashes across the suite).
"""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import mcp_hangar.server.tools.batch as batch_mod
from mcp_hangar.context import get_identity_context, identity_context_var
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.value_objects import Principal, PrincipalId, PrincipalType
from mcp_hangar.server.tools.batch import call_as, hangar_call
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


# --- the facade's `invoke` (#1453) -----------------------------------------------
#
# `call_as` is `hangar_call`'s path for a caller the embedder names: the
# principal is authorized as a request's is, and bound as the caller's identity,
# tenant included, while the executor runs.


def _identity_seeing_executor():
    """An executor stub that succeeds, and the caller identity bound each time it ran."""
    executor = _make_executor()
    succeed = executor.execute.side_effect
    seen = []

    def _execute(**kwargs):
        seen.append(get_identity_context())
        return succeed(**kwargs)

    executor.execute.side_effect = _execute
    return executor, seen


def _tenant_caller() -> Principal:
    return Principal(id=PrincipalId("agent-a"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id="tenant:a")


def test_call_as_an_anonymous_caller_is_denied_where_auth_is_configured() -> None:
    authz = MagicMock()
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = call_as(Principal.anonymous(), "svc", "do_thing", {})

    (call,) = result["results"]
    assert (call["error_type"], call["error"]) == ("AuthorizationDenied", "Authentication required to invoke tools")
    executor.execute.assert_not_called()
    authz.authorize.assert_not_called()


def test_call_as_an_anonymous_caller_carries_no_tenant_where_auth_is_off() -> None:
    executor, seen = _identity_seeing_executor()
    with _patched(authz_middleware=None, executor=executor):
        result = call_as(Principal.anonymous(), "svc", "do_thing", {})

    assert result["results"][0]["success"] is True
    (identity,) = seen
    assert (identity.caller.principal_type, identity.caller.tenant_id) == ("anonymous", None)


def test_call_as_authorizes_the_principal_and_binds_its_tenant() -> None:
    authz = MagicMock()
    authz.authorize.return_value = None
    executor, seen = _identity_seeing_executor()
    caller = _tenant_caller()
    with _patched(authz_middleware=authz, executor=executor):
        result = call_as(caller, "svc", "do_thing", {"x": 1}, timeout=12.0)

    assert result["results"][0]["success"] is True
    assert authz.authorize.call_args.kwargs == {
        "principal": caller,
        "action": "invoke",
        "resource_type": "tool",
        "resource_id": "do_thing",
    }
    (identity,) = seen
    assert (identity.caller.user_id, identity.caller.tenant_id) == ("agent-a", "tenant:a")
    # Released once the call returns: a facade worker thread is reused.
    assert identity_context_var.get() is None
    (spec,) = executor.execute.call_args.kwargs["calls"]
    assert (spec.mcp_server, spec.tool, spec.arguments) == ("svc", "do_thing", {"x": 1})
    assert executor.execute.call_args.kwargs["global_timeout"] == 12.0


def test_call_as_a_principal_lacking_tool_invoke_is_denied() -> None:
    authz = MagicMock()
    authz.authorize.side_effect = AccessDeniedError(principal_id="agent-a", action="invoke", resource="tool")
    executor = _make_executor()
    with _patched(authz_middleware=authz, executor=executor):
        result = call_as(_tenant_caller(), "svc", "dangerous", {})

    assert result["results"][0]["error_type"] == "AuthorizationDenied"
    executor.execute.assert_not_called()
