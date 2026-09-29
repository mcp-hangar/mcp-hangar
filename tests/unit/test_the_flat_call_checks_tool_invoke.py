"""The front door's flat ``tools/call`` checks ``tool:invoke`` as ``hangar_call`` does (#1622).

``hangar_call`` refuses a caller without ``tool:invoke`` in ``_authorize_calls``
before anything runs (#389). The flat call went straight to the executor, so a
``viewer`` could invoke on the front door a tool ``hangar_call`` refused it.

Every mode is driven down both paths with one table, so the two cannot drift:
auth off, stdio (ADR-026: no request, so no principal on it), a missing or
anonymous principal, and an API-key and a JWT principal holding ``viewer`` or
``developer``. The authorizer is the shipped RBAC one; only the executor and
the flat map are stood in for, and the executor records what reached it.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import anyio
import jwt
import pytest

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig, StaticSecretTokenValidator
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.auth.stdio_principal import clear_stdio_principal
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.events import ToolCallRefused
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import flat_tool_projection
from mcp_hangar.server.api.sessions import get_session_suspension_registry
from mcp_hangar.server.bootstrap import _declare_stdio_principal
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.models import BatchResult, CallResult, CallSpec

_SECRET = "a-test-only-hs256-secret-that-is-long-enough-for-pyjwt"
_SERVER, _TOOL, _TENANT = "math", "add", "t1"
_API_KEY_CALLER = "svc:caller"
_JWT_CALLER = "user:jwt-caller"
_NEED_AUTH = "Authentication required to invoke tools"
_NEED_INVOKE = f"Not authorized to invoke tool '{_TOOL}': tool:invoke permission required"
_PATHS = ("hangar_call", "flat")


# --- the callers ---------------------------------------------------------------


def _api_key_principal() -> Principal:
    """What the API-key authenticator produces: a service account with a tenant."""
    return Principal(id=PrincipalId(_API_KEY_CALLER), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=_TENANT)


def _jwt_principal() -> Principal:
    """The principal the served JWT authenticator produces for a signed token."""
    now = int(time.time())
    token = jwt.encode(
        {"sub": _JWT_CALLER, "tenant_id": _TENANT, "iat": now, "exp": now + 600}, _SECRET, algorithm="HS256"
    )
    authenticator = JWTAuthenticator(
        OIDCConfig(issuer="https://issuer.example", audience="hangar"), StaticSecretTokenValidator(_SECRET)
    )
    return authenticator.authenticate(
        AuthRequest(headers={"authorization": f"Bearer {token}"}, source_ip="127.0.0.1", method="POST", path="/mcp")
    )


def _stdio_config(role: str) -> dict[str, Any]:
    """An ``auth.stdio.principal`` block declaring *role*, as bootstrap reads it."""
    return {"auth": {"stdio": {"principal": {"id": "local-user", "tenant_id": "local", "roles": [role]}}}}


def _request(principal: Principal | None) -> SimpleNamespace:
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(state=SimpleNamespace(auth=auth), headers={}, client=SimpleNamespace(host="127.0.0.1"))


# --- the served app, stood in for where it must be ------------------------------


class _Gateway:
    """The application context both paths read, and the executor both dispatch through."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, auth: bool) -> None:
        self.roles = InMemoryRoleStore()
        self.events: list[Any] = []
        self.executed: list[CallSpec] = []
        components = SimpleNamespace(
            enabled=auth, authz_middleware=AuthorizationMiddleware(authorizer=RBACAuthorizer(self.roles))
        )
        app = SimpleNamespace(auth_components=components, event_bus=SimpleNamespace(publish=self.events.append))
        monkeypatch.setattr(batch, "get_context", lambda: app)
        monkeypatch.setattr(batch, "validate_batch", lambda *_a, **_k: [])
        monkeypatch.setattr(batch, "_executor", self)
        monkeypatch.setattr(flat_tool_projection, "_build_flat_map", lambda _tenant: {_TOOL: (_SERVER, _TOOL)})
        monkeypatch.setattr(flat_tool_projection, "_member_to_group", lambda: {})
        monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: frozenset())
        handlers: dict[str, Any] = {}

        class _Low:
            def add_request_handler(self, method: str, _params_type: Any, handler: Any) -> None:
                handlers[method] = handler

        flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))
        self._flat = handlers["tools/call"]

    def execute(self, *, batch_id: str, calls: list[CallSpec], **_kw: Any) -> BatchResult:
        self.executed.extend(calls)
        results = [
            CallResult(
                index=c.index, call_id=c.call_id, success=True, result={"content": [{"type": "text", "text": "3"}]}
            )
            for c in calls
        ]
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=0.0,
            results=results,
        )

    def call(self, path: str, principal: Principal | None, *, request: bool = True) -> str | None:
        """Make one call of add down *path*; return None when served, else the refusal text."""
        req = _request(principal) if request else None
        if path == "hangar_call":
            ctx = SimpleNamespace(request_context=SimpleNamespace(request=req))
            out = hangar_call([{"mcp_server": _SERVER, "tool": _TOOL, "arguments": {}}], ctx=ctx)
            [result] = out["results"]
            return None if result["success"] else result["error"]

        async def _run() -> Any:
            ctx = SimpleNamespace(request=req, meta=None, session=None)
            return await self._flat(ctx, SimpleNamespace(name=_TOOL, arguments={}))

        result = anyio.run(_run)
        if not (getattr(result, "is_error", None) or getattr(result, "isError", None)):
            return None
        return " ".join(block.text for block in result.content)

    def refusals(self) -> list[ToolCallRefused]:
        return [e for e in self.events if isinstance(e, ToolCallRefused)]


@pytest.fixture(autouse=True)
def _clean() -> Any:
    token = identity_context_var.set(None)
    get_session_suspension_registry().clear()
    clear_stdio_principal()
    yield
    clear_stdio_principal()
    identity_context_var.reset(token)


# --- the modes, down both paths ----------------------------------------------


@pytest.mark.parametrize("path", _PATHS)
class TestEveryModeDecidesAsHangarCall:
    def test_auth_off_refuses_nothing(self, monkeypatch, path) -> None:
        gateway = _Gateway(monkeypatch, auth=False)
        gateway.roles.assign_role(_API_KEY_CALLER, "viewer")

        assert gateway.call(path, _api_key_principal()) is None
        assert gateway.call(path, None) is None
        assert gateway.refusals() == [] and len(gateway.executed) == 2

    @pytest.mark.parametrize(
        ("auth", "declared", "refusal", "reason", "roles"),
        [
            (False, "viewer", None, None, ()),
            (True, "developer", None, None, ("developer",)),
            (True, "viewer", _NEED_INVOKE, "tool_invoke_denied", ()),
            (True, None, _NEED_AUTH, "unauthenticated", ()),
        ],
        ids=["auth-off", "developer", "viewer", "none-declared"],
    )
    def test_stdio_decides_on_the_declared_principal(
        self, monkeypatch, path, auth, declared, refusal, reason, roles
    ) -> None:
        """ADR-026: with no request, the caller is the declared principal, decided on its roles."""
        gateway = _Gateway(monkeypatch, auth=auth)
        if declared is not None:
            _declare_stdio_principal(_stdio_config(declared), stdio=True)

        assert gateway.call(path, None, request=False) == refusal
        assert [(e.gate, e.gate_reason) for e in gateway.refusals()] == ([("authorization", reason)] if reason else [])
        if refusal is None:
            [spec] = gateway.executed
            assert spec.caller_roles == roles
        else:
            assert gateway.executed == []
            [event] = gateway.refusals()
            assert event.identity_context is None or event.identity_context["user_id"] == "local-user"

    @pytest.mark.parametrize(
        "principal",
        [None, Principal.anonymous(), _api_key_principal()],
        ids=["no-principal", "anonymous", "viewer"],
    )
    def test_a_request_never_borrows_the_declared_principal(self, monkeypatch, path, principal) -> None:
        """A process with a declared ``developer`` still decides an HTTP request on the request's caller."""
        gateway = _Gateway(monkeypatch, auth=True)
        gateway.roles.assign_role(_API_KEY_CALLER, "viewer")
        _declare_stdio_principal(_stdio_config("developer"), stdio=True)

        refusal = gateway.call(path, principal)

        assert refusal == (_NEED_INVOKE if principal is not None and not principal.is_anonymous() else _NEED_AUTH)
        assert gateway.executed == []

    @pytest.mark.parametrize("anonymous", [False, True], ids=["missing", "anonymous"])
    def test_no_principal_is_refused_as_unauthenticated(self, monkeypatch, path, anonymous) -> None:
        gateway = _Gateway(monkeypatch, auth=True)

        refusal = gateway.call(path, Principal.anonymous() if anonymous else None)

        assert refusal == _NEED_AUTH
        assert gateway.executed == []
        assert [(e.gate, e.gate_reason) for e in gateway.refusals()] == [("authorization", "unauthenticated")]

    @pytest.mark.parametrize(
        ("caller", "principal"),
        [(_API_KEY_CALLER, _api_key_principal), (_JWT_CALLER, _jwt_principal)],
        ids=["api-key", "jwt"],
    )
    def test_a_viewer_is_refused(self, monkeypatch, path, caller, principal) -> None:
        gateway = _Gateway(monkeypatch, auth=True)
        gateway.roles.assign_role(caller, "viewer")

        refusal = gateway.call(path, principal())

        assert refusal == _NEED_INVOKE
        assert gateway.executed == []
        [event] = gateway.refusals()
        assert (event.gate, event.gate_reason) == ("authorization", "tool_invoke_denied")
        assert (event.mcp_server_id, event.tool_name) == (_SERVER, _TOOL)
        assert event.identity_context is not None
        assert (event.identity_context["user_id"], event.identity_context["roles"]) == (caller, [])

    @pytest.mark.parametrize(
        ("caller", "principal"),
        [(_API_KEY_CALLER, _api_key_principal), (_JWT_CALLER, _jwt_principal)],
        ids=["api-key", "jwt"],
    )
    def test_a_developer_is_served_and_carries_its_role(self, monkeypatch, path, caller, principal) -> None:
        gateway = _Gateway(monkeypatch, auth=True)
        gateway.roles.assign_role(caller, "developer")

        assert gateway.call(path, principal()) is None
        [spec] = gateway.executed
        assert (spec.mcp_server, spec.tool, spec.caller_roles) == (_SERVER, _TOOL, ("developer",))
        assert gateway.refusals() == []


def test_the_flat_call_runs_the_shared_check(monkeypatch) -> None:
    """Not a second copy of it: the one ``hangar_call`` runs, called from the flat handler."""
    gateway = _Gateway(monkeypatch, auth=True)
    gateway.roles.assign_role(_API_KEY_CALLER, "developer")
    seen: list[list[dict[str, Any]]] = []
    real = batch._authorize_calls

    def spy(calls: list[dict[str, Any]], *args: Any, **kwargs: Any) -> Any:
        seen.append(calls)
        return real(calls, *args, **kwargs)

    monkeypatch.setattr(batch, "_authorize_calls", spy)

    assert gateway.call("flat", _api_key_principal()) is None
    assert seen == [[{"mcp_server": _SERVER, "tool": _TOOL}]]
