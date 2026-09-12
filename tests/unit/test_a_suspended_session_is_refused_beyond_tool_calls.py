"""A suspended session is refused on every method that reaches an upstream (GHSA-fhwh-fmq2-7m5c).

The first pass covered tool calls. A suspended session could still poll, cancel
and answer its relayed tasks, fetch and list prompts, complete arguments, read
and list resources, and hold a subscription open -- each of them upstream
traffic on the caller's behalf. Each of those handlers now asks the same guard
first, and refuses with one JSON-RPC error: ``-32600``, the fixed message, and
``data.reason == "session_suspended"``.

Every test drives the real registered handler and asserts on the upstream: not
reached while the session is suspended, reached again once it is lifted. The
session-id claim, now configurable per issuer, is pinned at the end.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import jwt
import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.application.event_handlers.session_suspension_projection import SessionSuspensionProjection
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.application.tasks.governed_task_store import GovernedTaskStore
from mcp_hangar.auth.config import parse_auth_config
from mcp_hangar.auth.infrastructure.jwt_authenticator import JWTAuthenticator, OIDCConfig, StaticSecretTokenValidator
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.authentication import AuthRequest
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.session_suspension import VERIFIED_SESSION_ID_KEY
from mcp_hangar.domain.events import SessionSuspended, SessionUnsuspended
from mcp_hangar.domain.services import subscription_relay as sink
from mcp_hangar.domain.services.task_consent import TaskConsentGate
from mcp_hangar.domain.services.task_ownership import TaskOwner
from mcp_hangar.domain.value_objects.identity import CallerIdentity, IdentityContext
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.fastmcp_server import asgi
from mcp_hangar.fastmcp_server import prompt_proxy as pp
from mcp_hangar.fastmcp_server import resource_link_read_through as rt
from mcp_hangar.fastmcp_server import subscription_relay as sr
from mcp_hangar.fastmcp_server.task_relay_handlers import register_task_relay_handlers
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.session_suspension import InMemorySessionSuspensionRegistry
from mcp_hangar.server.api.sessions import get_session_suspension_registry
from mcp_hangar.server.session_guard import (
    SESSION_SUSPENDED_CODE,
    SESSION_SUSPENDED_MESSAGE,
    SESSION_SUSPENDED_REASON,
)
from mcp_hangar.tasks_wire import EXTENSION_ID

_SUSPENDED = (SESSION_SUSPENDED_CODE, SESSION_SUSPENDED_MESSAGE, {"reason": SESSION_SUSPENDED_REASON})
_SECRET = "a-test-only-hs256-secret-that-is-long-enough-for-pyjwt"


# --- the caller ------------------------------------------------------------


def _principal() -> Principal:
    return Principal(id=PrincipalId("svc:agent"), type=PrincipalType.SERVICE_ACCOUNT, tenant_id="t1")


def _ctx(*, session: str | None = "s-1", version: str | None = "2026-07-28", task_id: str = "T1") -> SimpleNamespace:
    """A `ServerRequestContext` as the lowlevel handlers receive it.

    Carries what the task ladder checks too (version, declared extension,
    ``Mcp-Name``), so a refusal can only come from the suspension.
    """
    headers = {"mcp-name": task_id}
    if session is not None:
        headers["x-session-id"] = session
    request = SimpleNamespace(
        state=SimpleNamespace(auth=SimpleNamespace(principal=_principal())),
        headers=headers,
        client=SimpleNamespace(host="127.0.0.1"),
    )
    client_session = SimpleNamespace(
        protocol_version=version,
        client_params=SimpleNamespace(capabilities=SimpleNamespace(extensions={EXTENSION_ID: {}})),
    )
    return SimpleNamespace(request=request, session=client_session, meta=None)


def _refusal(exc: BaseException) -> tuple[Any, Any, Any]:
    error = getattr(exc, "error", None) or exc
    return getattr(error, "code", None), getattr(error, "message", str(exc)), getattr(error, "data", None)


class _Low:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self._request_handlers: dict[str, Any] = {"subscriptions/listen": object()}

    def add_request_handler(self, method: str, _params_type: Any, handler: Any) -> None:
        self.handlers[method] = handler
        self._request_handlers[method] = handler


# --- fixtures ---------------------------------------------------------------


@pytest.fixture(autouse=True)
def registry():
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
    monkeypatch.delenv("MCP_TRUSTED_PROXIES", raising=False)
    asgi._forwarded_session_extractor.cache_clear()
    yield
    asgi._forwarded_session_extractor.cache_clear()


def _upstream_task(status: str = "working") -> dict[str, Any]:
    return {
        "taskId": "T1",
        "status": status,
        "createdAt": "2020-01-01T00:00:00Z",
        "lastUpdatedAt": "2020-01-01T00:00:00Z",
        "ttl": 60_000,
    }


@pytest.fixture
def tasks():
    """The real task relay handlers over a real store holding T1 for this caller."""
    upstream: list[str] = []

    def router(_target: str, method: str, _params: dict[str, Any], _timeout: float) -> Any:
        upstream.append(method)
        if method == "tasks/cancel":
            return {"error": {"code": -32000, "message": "not yet"}}  # unconfirmed: T1 is kept
        return {"result": _upstream_task()}

    store = GovernedTaskStore(event_publisher=lambda _event: None)
    owner = CallerIdentity(
        user_id="svc:agent", agent_id=None, session_id=None, principal_type="service", tenant_id="t1"
    )
    token = identity_context_var.set(IdentityContext(caller=owner))
    try:
        task = store.mint_from_upstream(_upstream_task())
        store.register_relayed_task(target_server_id="S1", task=task, expected_owner=TaskOwner("t1", "svc:agent"))
    finally:
        identity_context_var.reset(token)

    low = _Low()
    register_task_relay_handlers(SimpleNamespace(_mcp_server=low), store, TaskConsentGate(), router)
    return SimpleNamespace(handlers=low.handlers, upstream=upstream)


@pytest.fixture
def front_door():
    with patch("mcp_hangar.domain.services.tool_access_resolver.is_front_door", return_value=True):
        yield


@pytest.fixture
def prompts(front_door, monkeypatch):
    upstream: list[str] = []
    greet = {"name": "greet", "description": "Say hello"}

    def relay(_server: str, method: str, _params: dict[str, Any]) -> dict[str, Any]:
        upstream.append(method)
        if method == "completion/complete":
            return {"result": {"completion": {"values": ["bob"]}}}
        return {"result": {"messages": [{"role": "user", "content": {"type": "text", "text": "hi"}}]}}

    def prompt_map(_tenant: str | None) -> dict[str, Any]:
        upstream.append("prompts/list")
        return {"greet": ("server_a", greet)}

    monkeypatch.setattr(pp, "_relay", relay)
    monkeypatch.setattr(pp, "_build_prompt_map", prompt_map)
    monkeypatch.setattr(pp, "_completion_target", lambda _tenant, _ref: "server_a")
    low = _Low()
    with patch("mcp_hangar.fastmcp_server.prompt_proxy.lowlevel_server", return_value=low):
        assert pp.maybe_register_prompt_proxy(object())
    return SimpleNamespace(handlers=low.handlers, upstream=upstream)


@pytest.fixture
def resources(front_door, monkeypatch):
    upstream: list[str] = []

    class _Guard:
        async def enforce(self, *_args: Any) -> Any:
            return SimpleNamespace(allowed=True)

    def relay_read(_server: str, uri: str) -> dict[str, Any]:
        upstream.append("resources/read")
        return {"result": {"contents": [{"uri": uri, "text": "hi"}]}}

    def catalog(_tenant: str | None, _listing: Any) -> list[dict[str, Any]]:
        upstream.append("resources/list")
        return []

    monkeypatch.setattr(rt, "_resolve_target", lambda _tenant, _uri: ("server_a", "demo://doc/1"))
    monkeypatch.setattr(rt, "_ui_guard", lambda: _Guard())
    monkeypatch.setattr(rt, "_relay_read", relay_read)
    monkeypatch.setattr(rt, "_build_catalog", catalog)
    rt._links.clear()
    low = _Low()
    with patch("mcp_hangar.fastmcp_server.resource_link_read_through.lowlevel_server", return_value=low):
        assert rt.maybe_register_resource_read_through(object())
    yield SimpleNamespace(handlers=low.handlers, upstream=upstream)
    rt._links.clear()


@pytest.fixture
def subscriptions(front_door, monkeypatch):
    upstream: list[str] = []

    async def listen(_ctx: Any, _params: Any) -> str:
        return "stream-done"

    monkeypatch.setattr(pp, "_upstream_ids", lambda _tenant: ["server_a"])
    monkeypatch.setattr(sr, "_honored_targets", lambda _tenant, _requested: [("hangar://a/x", "server_a", "x")])
    monkeypatch.setattr(sr, "_acquire_upstream", lambda _targets: upstream.append("resources/subscribe"))
    monkeypatch.setattr(sr, "_release_upstream", lambda _targets: None)
    low = _Low()
    with (
        patch("mcp_hangar.fastmcp_server.subscription_relay.lowlevel_server", return_value=low),
        patch("mcp.server.subscriptions.ListenHandler", lambda _bus: listen),
    ):
        assert sr.maybe_register_subscription_relay(object())
    yield SimpleNamespace(handlers=low.handlers, upstream=upstream)
    sr._upstream_refs.clear()
    sr._loop = None
    sink.clear_sink()


def _listen_params() -> Any:
    from mcp_types import SubscriptionFilter, SubscriptionsListenRequestParams

    return SubscriptionsListenRequestParams(notifications=SubscriptionFilter(resource_subscriptions=["hangar://a/x"]))


def _complete_params() -> Any:
    from mcp_types import CompleteRequestParams

    return CompleteRequestParams.model_validate(
        {"ref": {"type": "ref/prompt", "name": "greet"}, "argument": {"name": "who", "value": "b"}}
    )


async def _served_then_refused_then_served(registry, handler, params, upstream: list[str]) -> None:
    await handler(_ctx(), params)
    reached = len(upstream)
    assert reached, "the served call never reached the upstream: this test would prove nothing"

    registry.suspend("s-1")
    with pytest.raises(McpError) as excinfo:
        await handler(_ctx(), params)
    assert _refusal(excinfo.value) == _SUSPENDED
    assert len(upstream) == reached, "a suspended session reached the upstream"

    registry.unsuspend("s-1")
    await handler(_ctx(), params)
    assert len(upstream) > reached


# --- tasks/* ------------------------------------------------------------------

_TASK_CALLS = [
    ("tasks/get", SimpleNamespace(task_id="T1")),
    ("tasks/cancel", SimpleNamespace(task_id="T1")),
    ("tasks/update", SimpleNamespace(task_id="T1", input_responses={})),
]


class TestTheTaskRelay:
    @pytest.mark.parametrize(("method", "params"), _TASK_CALLS, ids=[m for m, _ in _TASK_CALLS])
    async def test_served_then_refused_then_served(self, registry, tasks, method, params) -> None:
        await _served_then_refused_then_served(registry, tasks.handlers[method], params, tasks.upstream)

    @pytest.mark.parametrize(("method", "params"), _TASK_CALLS, ids=[m for m, _ in _TASK_CALLS])
    async def test_the_refusal_precedes_the_capability_ladder(self, registry, tasks, method, params) -> None:
        # A legacy-era caller is refused -32601 by the ladder. Suspended, it
        # gets the suspension instead: the check runs first.
        registry.suspend("s-1")

        with pytest.raises(McpError) as excinfo:
            await tasks.handlers[method](_ctx(version="2025-06-18"), params)

        assert _refusal(excinfo.value) == _SUSPENDED

    async def test_another_session_and_no_session_are_served(self, registry, tasks) -> None:
        registry.suspend("s-1")

        await tasks.handlers["tasks/get"](_ctx(session="s-2"), SimpleNamespace(task_id="T1"))
        await tasks.handlers["tasks/get"](_ctx(session=None), SimpleNamespace(task_id="T1"))

        assert tasks.upstream.count("tasks/get") == 2


# --- prompts, completions, resources, subscriptions ------------------------------


class TestTheFrontDoorsOtherUpstreamPaths:
    async def test_prompts_list(self, registry, prompts) -> None:
        await _served_then_refused_then_served(
            registry, prompts.handlers["prompts/list"], SimpleNamespace(), prompts.upstream
        )

    async def test_prompts_get(self, registry, prompts) -> None:
        await _served_then_refused_then_served(
            registry, prompts.handlers["prompts/get"], SimpleNamespace(name="greet", arguments={}), prompts.upstream
        )

    async def test_completion_complete(self, registry, prompts) -> None:
        await _served_then_refused_then_served(
            registry, prompts.handlers["completion/complete"], _complete_params(), prompts.upstream
        )

    async def test_resources_read(self, registry, resources) -> None:
        await _served_then_refused_then_served(
            registry,
            resources.handlers["resources/read"],
            SimpleNamespace(uri="hangar://server_a/demo://doc/1"),
            resources.upstream,
        )

    @pytest.mark.parametrize("method", ["resources/list", "resources/templates/list"])
    async def test_resource_listings(self, registry, resources, method) -> None:
        await _served_then_refused_then_served(
            registry, resources.handlers[method], SimpleNamespace(), resources.upstream
        )

    async def test_subscriptions_listen(self, registry, subscriptions) -> None:
        await _served_then_refused_then_served(
            registry, subscriptions.handlers["subscriptions/listen"], _listen_params(), subscriptions.upstream
        )

    async def test_an_untrusted_header_is_not_honoured(self, registry, prompts, monkeypatch) -> None:
        monkeypatch.setenv("MCP_TRUSTED_PROXIES", "203.0.113.7")
        asgi._forwarded_session_extractor.cache_clear()
        registry.suspend("s-1")

        await prompts.handlers["prompts/get"](_ctx(), SimpleNamespace(name="greet", arguments={}))

        assert "prompts/get" in prompts.upstream


# --- a peer's suspension ---------------------------------------------------------


class _Replica:
    def __init__(self, instance_id: str, log: InMemoryEventStore, registry: InMemorySessionSuspensionRegistry) -> None:
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = SessionSuspensionProjection(registry)
        self.bus.subscribe(SessionSuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.bus.subscribe(SessionUnsuspended, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, instance_id)


class TestAPeersSuspension:
    async def test_is_refused_on_a_task_poll_here_and_lifted_by_the_peers_lift(self, registry, tasks) -> None:
        log = InMemoryEventStore()
        here = _Replica("gateway-here", log, registry)
        peer = _Replica("gateway-peer", log, InMemorySessionSuspensionRegistry())
        get = tasks.handlers["tasks/get"]

        peer.bus.publish(SessionSuspended(session_id="s-1", reason="operator", source="api"))
        here.tailer.tick()
        with pytest.raises(McpError) as excinfo:
            await get(_ctx(), SimpleNamespace(task_id="T1"))
        assert _refusal(excinfo.value) == _SUSPENDED

        peer.bus.publish(SessionUnsuspended(session_id="s-1", source="api"))
        here.tailer.tick()
        await get(_ctx(), SimpleNamespace(task_id="T1"))


# --- the session-id claim ---------------------------------------------------------


_ISSUER_A = "https://issuer-a.example"
_ISSUER_B = "https://issuer-b.example"


class _CannedValidator:
    """Returns fixed claims: isolates the authenticator's claim selection."""

    def __init__(self, claims: dict[str, Any]) -> None:
        self._claims = claims

    def validate(self, _token: str) -> dict[str, Any]:
        return dict(self._claims)


def _claims(**extra: Any) -> dict[str, Any]:
    now = int(time.time())
    return {"iss": _ISSUER_A, "sub": "user:jwt", "iat": now, "exp": now + 600, **extra}


def _authenticate(config: OIDCConfig, claims: dict[str, Any], issuer_configs: dict[str, OIDCConfig] | None = None):
    authenticator = JWTAuthenticator(config, _CannedValidator(claims), issuer_configs=issuer_configs)
    return authenticator.authenticate(
        AuthRequest(headers={"authorization": "Bearer opaque"}, source_ip="127.0.0.1", method="POST", path="/mcp")
    )


class TestTheSessionIdClaim:
    def test_the_default_is_sid_everywhere(self) -> None:
        cfg = parse_auth_config({"enabled": True, "oidc": {"enabled": True, "issuer": _ISSUER_A, "audience": "x"}})

        assert cfg.oidc.session_id_claim == "sid"
        assert [entry.session_id_claim for entry in cfg.oidc.resolved_issuers()] == ["sid"]
        assert OIDCConfig(issuer=_ISSUER_A, audience="x").session_id_claim == "sid"

    def test_a_top_level_claim_is_inherited_and_an_issuer_may_override_it(self) -> None:
        cfg = parse_auth_config(
            {
                "enabled": True,
                "oidc": {
                    "enabled": True,
                    "session_id_claim": "session_ref",
                    "issuers": [
                        {"issuer": _ISSUER_A, "audience": "a"},
                        {"issuer": _ISSUER_B, "audience": "b", "session_id_claim": "sess"},
                    ],
                },
            }
        )

        inherited, overridden = cfg.oidc.resolved_issuers()
        assert inherited.session_id_claim == "session_ref"
        assert overridden.session_id_claim == "sess"

    def test_the_legacy_single_issuer_form_carries_it(self) -> None:
        cfg = parse_auth_config(
            {"enabled": True, "oidc": {"enabled": True, "issuer": _ISSUER_A, "session_id_claim": "session_ref"}}
        )

        assert cfg.oidc.resolved_issuers()[0].session_id_claim == "session_ref"

    def test_bootstrap_hands_each_issuer_its_claim(self) -> None:
        from mcp_hangar.auth.bootstrap import bootstrap_auth

        cfg = parse_auth_config(
            {
                "enabled": True,
                "oidc": {
                    "enabled": True,
                    "session_id_claim": "session_ref",
                    "issuers": [
                        {"issuer": _ISSUER_A, "audience": "a"},
                        {"issuer": _ISSUER_B, "audience": "b", "session_id_claim": "sess"},
                    ],
                },
            }
        )
        components = bootstrap_auth(cfg)

        (authenticator,) = [a for a in components.authn_middleware._authenticators if isinstance(a, JWTAuthenticator)]
        assert {issuer: c.session_id_claim for issuer, c in authenticator._issuer_configs.items()} == {
            _ISSUER_A: "session_ref",
            _ISSUER_B: "sess",
        }

    def test_the_authenticator_reads_the_configured_claim_and_only_that_one(self) -> None:
        config = OIDCConfig(issuer=_ISSUER_A, audience="x", session_id_claim="session_ref")

        custom = _authenticate(config, _claims(session_ref="s-custom", sid="s-sid"))
        sid_only = _authenticate(config, _claims(sid="s-sid"))

        assert custom.metadata[VERIFIED_SESSION_ID_KEY] == "s-custom"
        assert VERIFIED_SESSION_ID_KEY not in sid_only.metadata

    @pytest.mark.parametrize("value", ["has space", "x" * 129, 42, None])
    def test_a_configured_claim_is_held_to_the_same_shape(self, value: object) -> None:
        config = OIDCConfig(issuer=_ISSUER_A, audience="x", session_id_claim="session_ref")

        principal = _authenticate(config, _claims(session_ref=value))

        assert VERIFIED_SESSION_ID_KEY not in principal.metadata

    def test_each_issuer_reads_its_own_claim(self) -> None:
        config_a = OIDCConfig(issuer=_ISSUER_A, audience="a")
        config_b = OIDCConfig(issuer=_ISSUER_B, audience="b", session_id_claim="sess")
        issuers = {_ISSUER_A: config_a, _ISSUER_B: config_b}

        from_a = _authenticate(config_a, _claims(sid="s-a", sess="wrong"), issuers)
        from_b = _authenticate(config_a, _claims(iss=_ISSUER_B, sid="wrong", sess="s-b"), issuers)

        assert from_a.metadata[VERIFIED_SESSION_ID_KEY] == "s-a"
        assert from_b.metadata[VERIFIED_SESSION_ID_KEY] == "s-b"

    def test_a_signed_token_with_the_custom_claim_binds_the_session(self) -> None:
        token = jwt.encode(_claims(session_ref="s-custom"), _SECRET, algorithm="HS256")
        authenticator = JWTAuthenticator(
            OIDCConfig(issuer=_ISSUER_A, audience="x", session_id_claim="session_ref"),
            StaticSecretTokenValidator(_SECRET),
        )
        principal = authenticator.authenticate(
            AuthRequest(headers={"authorization": f"Bearer {token}"}, source_ip="127.0.0.1", method="POST", path="/")
        )
        ctx = SimpleNamespace(request=SimpleNamespace(state=SimpleNamespace(auth=SimpleNamespace(principal=principal))))

        identity = asgi.identity_for_request(ctx)

        assert identity is not None and identity.caller.session_id == "s-custom"
