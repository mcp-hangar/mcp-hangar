"""A suspended session is refused, on every invoke path and on every replica.

`POST /api/sessions/{id}/suspend` answered 200 and replicated the decision, and
nothing on a request path read it: a suspended session went on calling tools.
Two things were missing, and both are pinned here:

1. **A key to match.** Every served identity bridge set `session_id=None`. A
   served caller now carries a session id from the verified token's `sid`, or
   from an `x-session-id` header a trusted proxy set -- never from a header an
   arbitrary client set, because then a suspended client could send another id.
2. **A reader.** `hangar_call`, the front door's flat `tools/call`, and every
   `hangar_*` tool (the continuations included, through `authorize_tool`) ask
   the registry before doing anything for the caller.

The earlier tests of this feature defined "refused" as "the id is in the
registry", which is how it shipped refusing nothing. Everything here drives a
real chokepoint and asserts on the call.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import anyio
import jwt
import pytest
from starlette.datastructures import Headers
from structlog.testing import capture_logs

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.application.event_handlers.session_suspension_projection import SessionSuspensionProjection
from mcp_hangar.application.mcp.tooling import _CTX_KW, get_tool_authorizer, set_tool_authorizer
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig, StaticSecretTokenValidator
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.session_suspension import VERIFIED_SESSION_ID_KEY, is_well_formed_session_id
from mcp_hangar.domain.events import SessionSuspended, SessionUnsuspended
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import asgi, flat_tool_projection
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from mcp_hangar.server import session_guard
from mcp_hangar.server.api.sessions import get_session_suspension_registry
from mcp_hangar.server.session_guard import (
    SESSION_SUSPENDED_MESSAGE,
    SUSPENSION_UNCHECKED_MESSAGE,
    SessionSuspendedError,
    refuse_if_session_suspended,
)
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.models import BatchResult, CallResult
from mcp_hangar.server.tools.tool_permissions import SELF_AUTHORIZING_TOOLS, TOOL_PERMISSIONS, authorize_tool

_SECRET = "a-test-only-hs256-secret-that-is-long-enough-for-pyjwt"
_ONE_CALL = [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]
_TRUSTED_PEER = "127.0.0.1"  # the default MCP_TRUSTED_PROXIES
_UNTRUSTED_PEER = "203.0.113.9"


# --- the caller ------------------------------------------------------------


def _principal(name: str = "svc:agent", *, metadata: dict[str, object] | None = None) -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.SERVICE_ACCOUNT, tenant_id="t1", metadata=metadata)


def _request(
    principal: Principal | None,
    *,
    session: str | None = None,
    peer: str | None = _TRUSTED_PEER,
    headers: Any = None,
) -> SimpleNamespace:
    """The Starlette request the SDK hands a handler: auth state, headers and peer."""
    if headers is None:
        headers = {"x-session-id": session} if session is not None else {}
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    return SimpleNamespace(
        state=SimpleNamespace(auth=auth),
        headers=headers,
        client=SimpleNamespace(host=peer) if peer is not None else None,
    )


def _tool_ctx(principal: Principal | None, **request: Any) -> SimpleNamespace:
    """A high-level `Context`: what `hangar_call` and wrapped tools receive."""
    return SimpleNamespace(request_context=SimpleNamespace(request=_request(principal, **request)))


def _lowlevel_ctx(principal: Principal | None, **request: Any) -> SimpleNamespace:
    """A `ServerRequestContext`: what the front door's lowlevel handlers receive."""
    return SimpleNamespace(request=_request(principal, **request), meta=None, session=None)


def _token(**claims: Any) -> str:
    now = int(time.time())
    return jwt.encode({"sub": "user:jwt", "iat": now, "exp": now + 600, **claims}, _SECRET, algorithm="HS256")


def _authenticated(token: str) -> Principal:
    """The principal the served JWT authenticator produces for *token*."""
    authenticator = JWTAuthenticator(
        OIDCConfig(issuer="https://issuer.example", audience="hangar"), StaticSecretTokenValidator(_SECRET)
    )
    return authenticator.authenticate(
        AuthRequest(headers={"authorization": f"Bearer {token}"}, source_ip=_TRUSTED_PEER, method="POST", path="/mcp")
    )


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def registry():
    """The process registry -- the one the routes write and the chokepoints read."""
    process_registry = get_session_suspension_registry()
    process_registry.clear()
    yield process_registry
    process_registry.clear()


@pytest.fixture(autouse=True)
def unbound_identity():
    token = identity_context_var.set(None)
    yield
    identity_context_var.reset(token)


@pytest.fixture(autouse=True)
def trusted_proxies_from_env(monkeypatch):
    """Default proxies (loopback) unless a test sets the variable; re-read per test."""
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    asgi._forwarded_session_extractor.cache_clear()
    yield
    asgi._forwarded_session_extractor.cache_clear()


@pytest.fixture(autouse=True)
def installed_tool_authorizer():
    before = get_tool_authorizer()
    set_tool_authorizer(authorize_tool)
    yield
    set_tool_authorizer(before)


@pytest.fixture
def spy_batch(monkeypatch):
    """`hangar_call`'s gates and executor, counted, so "before any gate" is checkable."""
    seen = {"validated": 0, "authorized": 0, "executed": 0}

    def validate(*_a, **_k):
        seen["validated"] += 1
        return []

    authorize_calls = batch._authorize_calls

    def authorize(*a, **k):
        seen["authorized"] += 1
        return authorize_calls(*a, **k)

    def execute(*, batch_id: str, calls, **_kw) -> BatchResult:
        seen["executed"] += 1
        results = [CallResult(index=c.index, call_id=c.call_id, success=True, result={"value": 3}) for c in calls]
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=0.0,
            results=results,
        )

    monkeypatch.setattr(batch, "validate_batch", validate)
    monkeypatch.setattr(batch, "_authorize_calls", authorize)
    monkeypatch.setattr(batch._executor, "execute", execute)
    return seen


@pytest.fixture
def front_door(monkeypatch):
    """The real front-door `tools/call` handler over a one-tool map and a fake executor."""
    seen: dict[str, int] = {"mapped": 0, "executed": 0}

    def build_flat_map(_tenant):
        seen["mapped"] += 1
        return {"add": ("math", "add")}

    class _Executor:
        def execute(self, *, batch_id: str, calls, **_kw) -> BatchResult:
            seen["executed"] += 1
            ok = CallResult(
                index=0, call_id=batch_id, success=True, result={"content": [{"type": "text", "text": "3"}]}
            )
            return BatchResult(
                batch_id=batch_id, success=True, total=1, succeeded=1, failed=0, elapsed_ms=0.0, results=[ok]
            )

    monkeypatch.setattr(flat_tool_projection, "_build_flat_map", build_flat_map)
    monkeypatch.setattr(flat_tool_projection, "_member_to_group", lambda: {})
    monkeypatch.setattr("mcp_hangar.server.tools.tool_permissions.management_tools_for", lambda _ctx: frozenset())
    monkeypatch.setattr("mcp_hangar.server.tools.batch.BatchExecutor", _Executor)

    handlers: dict[str, Any] = {}

    class _Low:
        def add_request_handler(self, method, _params_type, handler):
            handlers[method] = handler

    flat_tool_projection.register_flat_tool_handlers(SimpleNamespace(_mcp_server=_Low()))

    def call(ctx: SimpleNamespace, name: str = "add") -> Any:
        async def _run() -> Any:
            return await handlers["tools/call"](ctx, SimpleNamespace(name=name, arguments={"a": 1, "b": 2}))

        return anyio.run(_run)

    return SimpleNamespace(call=call, seen=seen)


@pytest.fixture
def served_tools(monkeypatch):
    """The wrapped functions the shipped server registers, with the continuation cache spied."""
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    reached: list[str] = []

    def cache():
        reached.append("cache")
        return None

    monkeypatch.setattr("mcp_hangar.server.tools.continuation.get_response_cache", cache)
    server = build_serving_mcp_server()
    set_tool_authorizer(authorize_tool)
    return SimpleNamespace(fn=lambda name: server._tool_manager.get_tool(name).fn, reached=reached)


def _is_error(result: Any) -> bool:
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None))


def _text(result: Any) -> str:
    return " ".join(getattr(block, "text", "") or "" for block in getattr(result, "content", None) or [])


# --- 1. the key ----------------------------------------------------------------


class TestWhichSessionIdACallerCarries:
    def test_a_verified_tokens_sid_is_the_session(self) -> None:
        principal = _authenticated(_token(sid="s-jwt-1"))

        identity = asgi.identity_for_request(_tool_ctx(principal))

        assert principal.metadata[VERIFIED_SESSION_ID_KEY] == "s-jwt-1"
        assert identity is not None and identity.caller.session_id == "s-jwt-1"

    def test_a_header_cannot_replace_the_tokens_sid(self) -> None:
        # Even from a trusted proxy: the token is the credential, and a header
        # naming another session must not let its holder step out of a
        # suspension.
        principal = _authenticated(_token(sid="s-jwt-1"))

        identity = asgi.identity_for_request(_tool_ctx(principal, session="s-other"))

        assert identity is not None and identity.caller.session_id == "s-jwt-1"

    def test_a_token_without_a_sid_falls_back_to_a_trusted_header(self) -> None:
        principal = _authenticated(_token())

        identity = asgi.identity_for_request(_tool_ctx(principal, session="s-proxy"))

        assert VERIFIED_SESSION_ID_KEY not in principal.metadata
        assert identity is not None and identity.caller.session_id == "s-proxy"

    @pytest.mark.parametrize("sid", ["has space", "x" * 129, "semi;colon", 42])
    def test_a_sid_no_suspension_could_name_is_not_recorded(self, sid: object) -> None:
        principal = _authenticated(_token(sid=sid))

        assert VERIFIED_SESSION_ID_KEY not in principal.metadata

    def test_a_trusted_proxys_header_is_honoured(self) -> None:
        identity = asgi.identity_for_request(_tool_ctx(_principal(), session="s-1", peer=_TRUSTED_PEER))

        assert identity is not None and identity.caller.session_id == "s-1"

    def test_the_header_is_read_case_insensitively_off_a_real_request(self) -> None:
        headers = Headers({"X-Session-Id": "s-1"})

        identity = asgi.identity_for_request(_tool_ctx(_principal(), headers=headers))

        assert identity is not None and identity.caller.session_id == "s-1"

    def test_an_untrusted_clients_header_is_ignored(self) -> None:
        # The evasion this prevents: a client choosing its own session id could
        # step out of a suspension by sending another one.
        identity = asgi.identity_for_request(_tool_ctx(_principal(), session="s-1", peer=_UNTRUSTED_PEER))

        assert identity is not None and identity.caller.session_id is None

    def test_the_trusted_proxies_are_the_configured_ones(self, monkeypatch) -> None:
        monkeypatch.setenv("MCP_TRUSTED_PROXIES", "10.0.0.0/8")
        asgi._forwarded_session_extractor.cache_clear()

        from_proxy = asgi.identity_for_request(_tool_ctx(_principal(), session="s-1", peer="10.1.2.3"))
        from_loopback = asgi.identity_for_request(_tool_ctx(_principal(), session="s-1", peer="127.0.0.1"))

        assert from_proxy is not None and from_proxy.caller.session_id == "s-1"
        assert from_loopback is not None and from_loopback.caller.session_id is None

    def test_a_header_with_no_known_peer_is_ignored(self) -> None:
        identity = asgi.identity_for_request(_tool_ctx(_principal(), session="s-1", peer=None))

        assert identity is not None and identity.caller.session_id is None

    @pytest.mark.parametrize("value", ["has space", "x" * 129, "a\r\nb", ""])
    def test_a_header_no_suspension_could_name_is_dropped(self, value: str) -> None:
        # Carried on the identity it would reach audit records as caller text.
        identity = asgi.identity_for_request(_tool_ctx(_principal(), session=value))

        assert identity is not None and identity.caller.session_id is None

    def test_a_caller_with_neither_carries_no_session(self) -> None:
        identity = asgi.identity_for_request(_tool_ctx(_principal()))

        assert identity is not None and identity.caller.session_id is None

    def test_an_anonymous_principal_carries_a_trusted_session(self) -> None:
        identity = asgi.identity_for_request(_tool_ctx(Principal.anonymous(), session="s-anon"))

        assert identity is not None
        assert identity.caller.principal_type == "anonymous"
        assert identity.caller.session_id == "s-anon"

    def test_no_principal_is_no_identity(self) -> None:
        # Auth off: the bridge binds nothing, as before. See UPGRADE.md for why
        # suspension is not a control there.
        assert asgi.identity_for_request(_tool_ctx(None, session="s-1")) is None

    def test_the_bridge_binds_the_session(self) -> None:
        from mcp_hangar.context import get_identity_context

        token = asgi.bind_caller_identity(_lowlevel_ctx(_principal(), session="s-1"))
        try:
            identity = get_identity_context()
            assert identity is not None and identity.caller.session_id == "s-1"
        finally:
            asgi.release_caller_identity(token)

    def test_the_shape_check_matches_the_suspend_route(self) -> None:
        assert is_well_formed_session_id("abc_DEF-123")
        assert not is_well_formed_session_id("abc\n")
        assert not is_well_formed_session_id(None)


# --- 2. the reader ---------------------------------------------------------


class TestTheGuard:
    def test_no_session_is_never_refused(self, registry) -> None:
        registry.suspend("s-1")

        refuse_if_session_suspended("hangar_call", _tool_ctx(_principal()))

    def test_a_suspended_session_is_refused_with_a_fixed_message(self, registry) -> None:
        registry.suspend("s-1")

        with pytest.raises(SessionSuspendedError) as excinfo:
            refuse_if_session_suspended("hangar_call", _tool_ctx(_principal(), session="s-1"))

        assert str(excinfo.value) == SESSION_SUSPENDED_MESSAGE
        assert "s-1" not in str(excinfo.value)

    def test_the_bound_identity_is_the_one_asked_about(self, registry) -> None:
        registry.suspend("s-bound")
        caller = CallerIdentity(user_id="svc:agent", agent_id=None, session_id="s-bound", principal_type="service")
        token = identity_context_var.set(IdentityContext(caller=caller))
        try:
            with pytest.raises(SessionSuspendedError):
                refuse_if_session_suspended("flat_tool")
        finally:
            identity_context_var.reset(token)

    def test_a_registry_that_cannot_answer_refuses(self, monkeypatch) -> None:
        def broken():
            raise RuntimeError("registry down")

        monkeypatch.setattr(session_guard, "_registry", broken)

        with pytest.raises(SessionSuspendedError) as excinfo:
            refuse_if_session_suspended("hangar_call", _tool_ctx(_principal(), session="s-1"))

        assert str(excinfo.value) == SUSPENSION_UNCHECKED_MESSAGE

    def test_a_request_that_cannot_be_read_carries_no_session(self, registry) -> None:
        # A context outside a request raises on access; that is no request, and
        # refusing it would refuse every stdio call.
        class _Unreadable:
            @property
            def request_context(self):
                raise ValueError("outside a request")

        registry.suspend("s-1")

        refuse_if_session_suspended("hangar_call", _Unreadable())

    def test_the_log_line_names_only_the_chokepoint_and_the_session(self, registry, front_door) -> None:
        registry.suspend("s-1")

        with capture_logs() as logs:
            result = front_door.call(_lowlevel_ctx(_principal(), session="s-1"), name="evil\ncaller-text")

        refusals = [entry for entry in logs if entry["event"] == "session_suspended_call_refused"]
        assert refusals == [
            {
                "event": "session_suspended_call_refused",
                "log_level": "warning",
                "chokepoint": "flat_tool",
                "session_id": "s-1",
            }
        ]
        assert all("caller-text" not in repr(entry) for entry in logs)
        assert "caller-text" not in _text(result)


# --- 3. every chokepoint: fail before, pass after -----------------------------


class TestHangarCall:
    def test_served_then_refused_before_any_gate_then_served(self, registry, spy_batch) -> None:
        ctx = _tool_ctx(_principal(), session="s-1")
        assert hangar_call(calls=list(_ONE_CALL), ctx=ctx)["success"] is True

        registry.suspend("s-1")
        before = dict(spy_batch)
        with pytest.raises(SessionSuspendedError):
            hangar_call(calls=list(_ONE_CALL), ctx=ctx)
        assert spy_batch == before, "a gate or the executor ran for a suspended session"

        registry.unsuspend("s-1")
        assert hangar_call(calls=list(_ONE_CALL), ctx=ctx)["success"] is True

    def test_an_empty_batch_is_refused_too(self, registry, spy_batch) -> None:
        registry.suspend("s-1")

        with pytest.raises(SessionSuspendedError):
            hangar_call(calls=[], ctx=_tool_ctx(_principal(), session="s-1"))

    def test_a_token_session_is_refused(self, registry, spy_batch) -> None:
        registry.suspend("s-jwt-1")
        ctx = _tool_ctx(_authenticated(_token(sid="s-jwt-1")), session="s-other")

        with pytest.raises(SessionSuspendedError):
            hangar_call(calls=list(_ONE_CALL), ctx=ctx)
        assert spy_batch["executed"] == 0

    def test_an_untrusted_header_naming_a_suspended_session_is_not_refused(self, registry, spy_batch) -> None:
        registry.suspend("s-1")

        result = hangar_call(calls=list(_ONE_CALL), ctx=_tool_ctx(_principal(), session="s-1", peer=_UNTRUSTED_PEER))

        assert result["success"] is True

    def test_another_session_and_no_session_are_served(self, registry, spy_batch) -> None:
        registry.suspend("s-1")

        assert hangar_call(calls=list(_ONE_CALL), ctx=_tool_ctx(_principal(), session="s-2"))["success"] is True
        assert hangar_call(calls=list(_ONE_CALL), ctx=_tool_ctx(_principal()))["success"] is True

    def test_the_executor_sees_the_session(self, spy_batch, monkeypatch) -> None:
        from mcp_hangar.context import get_identity_context

        seen: list[str | None] = []

        def execute(*, batch_id: str, calls, **_kw) -> BatchResult:
            identity = get_identity_context()
            seen.append(identity.caller.session_id if identity is not None else None)
            return BatchResult(batch_id=batch_id, success=True, total=0, succeeded=0, failed=0, elapsed_ms=0.0)

        monkeypatch.setattr(batch._executor, "execute", execute)

        hangar_call(calls=list(_ONE_CALL), ctx=_tool_ctx(_principal(), session="s-1"))

        assert seen == ["s-1"]


class TestTheFlatToolsCall:
    def test_served_then_refused_before_the_map_then_served(self, registry, front_door) -> None:
        ctx = _lowlevel_ctx(_principal(), session="s-1")
        assert not _is_error(front_door.call(ctx))

        registry.suspend("s-1")
        before = dict(front_door.seen)
        refused = front_door.call(ctx)
        assert _is_error(refused)
        assert _text(refused) == SESSION_SUSPENDED_MESSAGE
        assert front_door.seen == before, "the flat map or the executor ran for a suspended session"

        registry.unsuspend("s-1")
        assert not _is_error(front_door.call(ctx))

    def test_an_untrusted_header_is_not_honoured(self, registry, front_door) -> None:
        registry.suspend("s-1")

        assert not _is_error(front_door.call(_lowlevel_ctx(_principal(), session="s-1", peer=_UNTRUSTED_PEER)))


class TestTheManagementTools:
    @pytest.mark.parametrize("tool", sorted(set(TOOL_PERMISSIONS) | SELF_AUTHORIZING_TOOLS))
    def test_every_gated_tool_is_refused_for_a_suspended_session(self, registry, tool: str) -> None:
        ctx = _tool_ctx(_principal(), session="s-1")
        authorize_tool(tool, ctx)  # auth off: allowed

        registry.suspend("s-1")
        with pytest.raises(SessionSuspendedError):
            authorize_tool(tool, ctx)

        registry.unsuspend("s-1")
        authorize_tool(tool, ctx)

    def test_a_suspension_outranks_a_global_admin_grant(self, registry, monkeypatch) -> None:
        principal = _principal("svc:operator")
        store = InMemoryRoleStore()
        store.assign_role(principal_id=str(principal.id), role_name="admin")
        components = SimpleNamespace(enabled=True, authz_middleware=AuthorizationMiddleware(RBACAuthorizer(store)))
        monkeypatch.setattr(
            "mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components)
        )
        ctx = _tool_ctx(principal, session="s-1")
        authorize_tool("hangar_stop", ctx)

        registry.suspend("s-1")

        with pytest.raises(SessionSuspendedError):
            authorize_tool("hangar_stop", ctx)

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("hangar_fetch_continuation", {"continuation_id": "cont_abc"}),
            ("hangar_delete_continuation", {"continuation_id": "cont_abc"}),
        ],
    )
    def test_the_continuations_through_the_served_wrapper(self, registry, served_tools, tool, arguments) -> None:
        call = served_tools.fn(tool)
        ctx = _tool_ctx(_principal(), session="s-1")
        assert isinstance(call(**arguments, **{_CTX_KW: ctx}), dict)
        assert served_tools.reached == ["cache"]

        registry.suspend("s-1")
        with pytest.raises(SessionSuspendedError):
            call(**arguments, **{_CTX_KW: ctx})
        assert served_tools.reached == ["cache"], "the continuation body ran for a suspended session"

        registry.unsuspend("s-1")
        assert isinstance(call(**arguments, **{_CTX_KW: ctx}), dict)
        assert served_tools.reached == ["cache", "cache"]

    def test_a_management_tool_through_the_served_wrapper(self, registry, served_tools) -> None:
        call = served_tools.fn("hangar_list")
        ctx = _tool_ctx(_principal(), session="s-1")
        registry.suspend("s-1")

        with pytest.raises(SessionSuspendedError):
            call(**{_CTX_KW: ctx})


# --- 4. replicated suspensions --------------------------------------------


class _Replica:
    """One gateway over a shared log: its registry, its bus, its tail."""

    def __init__(self, instance_id: str, log: InMemoryEventStore, registry: InMemorySessionSuspensionRegistry) -> None:
        self.registry = registry
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = SessionSuspensionProjection(registry)
        self.bus.subscribe(SessionSuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.bus.subscribe(SessionUnsuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, instance_id)

    def suspends(self, session_id: str) -> None:
        self.bus.publish(SessionSuspended(session_id=session_id, reason="operator", source="api"))

    def lifts(self, session_id: str) -> None:
        self.bus.publish(SessionUnsuspended(session_id=session_id, source="api"))


@pytest.fixture(params=["here", "on_the_peer"])
def decided(request, registry):
    """Two replicas; this process is the one whose registry the chokepoints read.

    ``here``: this replica decides, the peer tails. ``on_the_peer``: the peer
    decides and this replica only learns it from the log -- the bypass the
    replicated registry was built to close, now checked on a call.
    """
    log = InMemoryEventStore()
    here = _Replica("gateway-here", log, registry)
    peer = _Replica("gateway-peer", log, InMemorySessionSuspensionRegistry())
    decider, follower = (here, peer) if request.param == "here" else (peer, here)
    return SimpleNamespace(decider=decider, follower=follower)


class TestAReplicatedSuspensionIsRefusedHere:
    def test_hangar_call(self, decided, spy_batch) -> None:
        ctx = _tool_ctx(_principal(), session="s-1")

        decided.decider.suspends("s-1")
        decided.follower.tailer.tick()
        with pytest.raises(SessionSuspendedError):
            hangar_call(calls=list(_ONE_CALL), ctx=ctx)

        decided.decider.lifts("s-1")
        decided.follower.tailer.tick()
        assert hangar_call(calls=list(_ONE_CALL), ctx=ctx)["success"] is True

    def test_the_flat_tools_call(self, decided, front_door) -> None:
        ctx = _lowlevel_ctx(_principal(), session="s-1")

        decided.decider.suspends("s-1")
        decided.follower.tailer.tick()
        assert _is_error(front_door.call(ctx))

        decided.decider.lifts("s-1")
        decided.follower.tailer.tick()
        assert not _is_error(front_door.call(ctx))

    def test_a_continuation(self, decided, served_tools) -> None:
        call = served_tools.fn("hangar_fetch_continuation")
        ctx = _tool_ctx(_principal(), session="s-1")

        decided.decider.suspends("s-1")
        decided.follower.tailer.tick()
        with pytest.raises(SessionSuspendedError):
            call(continuation_id="cont_abc", **{_CTX_KW: ctx})

        decided.decider.lifts("s-1")
        decided.follower.tailer.tick()
        assert isinstance(call(continuation_id="cont_abc", **{_CTX_KW: ctx}), dict)
