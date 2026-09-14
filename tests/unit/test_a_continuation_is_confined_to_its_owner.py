"""A continuation answers only the caller whose ``hangar_call`` produced it.

With truncation on, the rest of a truncated result is cached under its
continuation id. The cache was keyed on that id alone, so any caller holding it
could fetch or delete the payload: another tenant with a tenant-scoped
``tool:invoke``, or anyone at all with auth off. The INFO ``result_truncated``
line also printed the id in full.

Now the cache records the tenant and principal that made the call. It answers
anyone else exactly as it answers an id that does not exist, and no log line
carries the id's random suffix.

Everything here drives the served tools. ``hangar_call`` mints the continuation:
its executor is stubbed to return one large result, and the real truncation is
applied under the identity ``hangar_call`` binds. The registered, wrapped
``hangar_fetch_continuation`` and ``hangar_delete_continuation`` read it back.
Each call carries a request context whose principal the auth middleware would
have set. Both cache backends are covered; Redis runs against an in-process
fake client.
"""

from __future__ import annotations

import json
import secrets
import sys
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

import mcp_hangar.server.tools.batch as batch
from mcp_hangar.application.mcp.tooling import _CTX_KW, get_tool_authorizer, set_tool_authorizer
from mcp_hangar.auth.infrastructure.middleware import AuthorizationMiddleware
from mcp_hangar.auth.infrastructure.rbac_authorizer import InMemoryRoleStore, RBACAuthorizer
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType
from mcp_hangar.domain.value_objects.truncation import ContinuationId
from mcp_hangar.infrastructure.truncation import manager as manager_module
from mcp_hangar.infrastructure.truncation import memory_cache as memory_cache_module
from mcp_hangar.infrastructure.truncation import redis_cache as redis_cache_module
from mcp_hangar.server.bootstrap.truncation import init_truncation, reset_truncation
from mcp_hangar.server.tools import continuation as continuation_module
from mcp_hangar.server.tools.batch import executor as executor_module
from mcp_hangar.server.tools.batch import hangar_call
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import BatchResult, CallResult
from mcp_hangar.server.tools.tool_permissions import authorize_tool

#: What a caller is told for an id the cache does not hold.
_NOT_FOUND = {"found": False, "error": "Continuation not found (may have expired)"}

#: One tool result far over the budget below, so it is always truncated.
_BIG = {"rows": [f"row-{i:04d}-" + "x" * 40 for i in range(60)]}
_BIG_JSON = json.dumps(_BIG)

_TRUNCATION = {"enabled": True, "max_batch_size_bytes": 400, "min_per_response_bytes": 100, "cache_ttl_s": 300}
_ONE_CALL = {"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}


def _principal(name: str, tenant: str | None) -> Principal:
    return Principal(id=PrincipalId(name), type=PrincipalType.SERVICE_ACCOUNT, tenant_id=tenant)


ALICE = _principal("svc:alice", "A")
CAROL = _principal("svc:carol", "A")  # alice's tenant, another principal
BOB = _principal("svc:bob", "B")
FLEET = _principal("svc:fleet", None)  # no tenant; a global grant


def _tool_ctx(principal: Principal | None) -> SimpleNamespace:
    """A high-level `Context` carrying what the auth middleware left on the request."""
    auth = SimpleNamespace(principal=principal) if principal is not None else None
    request = SimpleNamespace(state=SimpleNamespace(auth=auth), headers={}, client=SimpleNamespace(host="127.0.0.1"))
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _random_id() -> str:
    return f"cont_{uuid.uuid4()}_0_{secrets.token_hex(4)}"


def _suffix(continuation_id: str) -> str:
    """The random part of an id: what a log reader must never see."""
    return continuation_id.rsplit("_", 1)[1]


# --- the cache backends -------------------------------------------------------


class _FakeRedis:
    """The four commands the Redis cache uses, over a dict."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def setex(self, key: str, _ttl: int, value: str) -> bool:
        self.values[key] = value
        return True

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def delete(self, *keys: str) -> int:
        return sum(1 for key in keys if self.values.pop(key, None) is not None)

    def ping(self) -> bool:
        return True


def _install(monkeypatch: pytest.MonkeyPatch, driver: str) -> _FakeRedis | None:
    reset_truncation()
    config = dict(_TRUNCATION)
    fake = None
    if driver == "redis":
        fake = _FakeRedis()
        module = SimpleNamespace(from_url=lambda *_a, **_k: fake, Redis=object)
        monkeypatch.setitem(sys.modules, "redis", module)
        config.update(cache_driver="redis", redis_url="redis://cache.invalid:6379/0")
    init_truncation({"truncation": config})
    return fake


@pytest.fixture(params=["memory", "redis"])
def backend(request, monkeypatch):
    fake = _install(monkeypatch, request.param)
    yield SimpleNamespace(driver=request.param, redis=fake)
    reset_truncation()


@pytest.fixture
def redis_backend(monkeypatch):
    fake = _install(monkeypatch, "redis")
    assert fake is not None
    yield fake
    reset_truncation()


# --- auth ---------------------------------------------------------------------


def _auth(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    components: Any = None
    if enabled:
        store = InMemoryRoleStore()
        store.assign_role("svc:alice", "developer", scope="tenant:A")
        store.assign_role("svc:carol", "developer", scope="tenant:A")
        store.assign_role("svc:bob", "developer", scope="tenant:B")
        store.assign_role("svc:fleet", "developer", scope="global")
        components = SimpleNamespace(enabled=True, authz_middleware=AuthorizationMiddleware(RBACAuthorizer(store)))
    monkeypatch.setattr("mcp_hangar.server.context.get_context", lambda: SimpleNamespace(auth_components=components))


@pytest.fixture
def auth_on(monkeypatch):
    _auth(monkeypatch, enabled=True)


@pytest.fixture
def auth_off(monkeypatch):
    _auth(monkeypatch, enabled=False)


@pytest.fixture(autouse=True)
def unbound_identity():
    token = identity_context_var.set(None)
    yield
    identity_context_var.reset(token)


@pytest.fixture(autouse=True)
def installed_tool_authorizer():
    before = get_tool_authorizer()
    set_tool_authorizer(authorize_tool)
    yield
    set_tool_authorizer(before)


# --- the served tools ---------------------------------------------------------


@pytest.fixture
def served(monkeypatch):
    """The wrapped continuation tools the shipped server registers."""
    from mcp_hangar.server.bootstrap import build_serving_mcp_server

    # The rate limiter is process state shared across the suite, and it is not
    # what is under test. The wrapper captures this callable at registration.
    monkeypatch.setattr(continuation_module, "check_rate_limit", lambda _key: None)
    server = build_serving_mcp_server()
    set_tool_authorizer(authorize_tool)
    tools = {
        name: server._tool_manager.get_tool(name).fn
        for name in ("hangar_fetch_continuation", "hangar_delete_continuation")
    }

    def fetch(principal: Principal | None, continuation_id: str, **kwargs: Any) -> dict:
        return tools["hangar_fetch_continuation"](
            continuation_id=continuation_id, **kwargs, **{_CTX_KW: _tool_ctx(principal)}
        )

    def delete(principal: Principal | None, continuation_id: str) -> dict:
        return tools["hangar_delete_continuation"](continuation_id=continuation_id, **{_CTX_KW: _tool_ctx(principal)})

    return SimpleNamespace(fetch=fetch, delete=delete)


@pytest.fixture
def mint(monkeypatch):
    """``hangar_call`` as *principal*, returning the continuation id its caller is handed.

    The executor is stubbed to return one large result; the truncation applied
    to it is the real one, on the thread and under the identity ``hangar_call``
    binds, exactly where the real executor applies it.
    """
    real = BatchExecutor()

    def execute(*, batch_id: str, calls, **_kw) -> BatchResult:
        results = [CallResult(index=c.index, call_id=c.call_id, success=True, result=_BIG) for c in calls]
        results = real._apply_batch_truncation(batch_id, results)
        return BatchResult(
            batch_id=batch_id,
            success=True,
            total=len(calls),
            succeeded=len(calls),
            failed=0,
            elapsed_ms=0.0,
            results=results,
        )

    monkeypatch.setattr(batch, "validate_batch", lambda *_a, **_k: [])
    monkeypatch.setattr(batch._executor, "execute", execute)

    def run(principal: Principal | None) -> str:
        response = hangar_call(calls=[dict(_ONE_CALL)], ctx=_tool_ctx(principal))
        (result,) = response["results"]
        assert result.get("truncated") is True and result.get("continuation_id"), response
        return str(result["continuation_id"])

    return run


# --- 1. anyone else gets the not-found answer ---------------------------------


_INTRUDERS = pytest.mark.parametrize(
    ("owner", "intruder"),
    [(ALICE, BOB), (ALICE, CAROL), (ALICE, FLEET), (FLEET, BOB)],
    ids=["another-tenant", "same-tenant-another-principal", "no-tenant-caller", "no-tenant-owner"],
)


@pytest.mark.usefixtures("backend", "auth_on")
class TestAnotherCallerIsToldTheIdDoesNotExist:
    @_INTRUDERS
    def test_a_fetch(self, served, mint, owner, intruder) -> None:
        continuation_id = mint(owner)

        answer = served.fetch(intruder, continuation_id)
        nonexistent = served.fetch(intruder, _random_id())

        assert answer == _NOT_FOUND
        assert json.dumps(answer) == json.dumps(nonexistent)

    @_INTRUDERS
    def test_a_paged_fetch(self, served, mint, owner, intruder) -> None:
        continuation_id = mint(owner)

        answer = served.fetch(intruder, continuation_id, offset=10, limit=20)
        nonexistent = served.fetch(intruder, _random_id(), offset=10, limit=20)

        assert json.dumps(answer) == json.dumps(nonexistent) == json.dumps(_NOT_FOUND)

    @_INTRUDERS
    def test_a_delete(self, served, mint, owner, intruder) -> None:
        continuation_id = mint(owner)

        answer = served.delete(intruder, continuation_id)

        assert answer == {"deleted": False, "continuation_id": continuation_id}
        assert served.fetch(owner, continuation_id)["found"] is True, "the intruder's delete removed the entry"

        # Byte-identical to the answer for the same id once it truly does not
        # exist, and to a random id's but for the id the answer echoes back.
        assert served.delete(owner, continuation_id)["deleted"] is True
        assert json.dumps(served.delete(intruder, continuation_id)) == json.dumps(answer)
        other = _random_id()
        assert json.dumps(served.delete(intruder, other)) == json.dumps(answer).replace(continuation_id, other)


# --- 2. the owner keeps it ----------------------------------------------------


@pytest.mark.usefixtures("backend", "auth_on")
class TestTheOwnerKeepsItsContinuation:
    def test_a_whole_fetch(self, served, mint) -> None:
        continuation_id = mint(ALICE)

        answer = served.fetch(ALICE, continuation_id)

        assert answer["found"] is True and answer["complete"] is True and answer["has_more"] is False
        assert answer["data"] == _BIG
        assert answer["total_size_bytes"] == len(_BIG_JSON.encode("utf-8"))

    def test_a_paged_fetch(self, served, mint) -> None:
        continuation_id = mint(ALICE)

        chunks: list[str] = []
        offset = 0
        while True:
            page = served.fetch(ALICE, continuation_id, offset=offset, limit=500)
            assert page["found"] is True and page["offset"] == offset
            chunks.append(page["data"])
            offset += len(page["data"].encode("utf-8"))
            if not page["has_more"]:
                break

        assert len(chunks) > 1
        assert "".join(chunks) == _BIG_JSON

    def test_a_delete(self, served, mint) -> None:
        continuation_id = mint(ALICE)

        assert served.delete(ALICE, continuation_id) == {"deleted": True, "continuation_id": continuation_id}
        assert served.fetch(ALICE, continuation_id) == _NOT_FOUND


# --- 3. auth off --------------------------------------------------------------


@pytest.mark.usefixtures("backend")
class TestWithAuthOff:
    """No identity on either side: the owner and the caller are both anonymous, as before."""

    def test_the_caller_fetches_and_deletes(self, auth_off, served, mint) -> None:
        continuation_id = mint(None)

        assert served.fetch(None, continuation_id)["data"] == _BIG
        assert served.fetch(None, continuation_id, offset=0, limit=100)["has_more"] is True
        assert served.delete(None, continuation_id)["deleted"] is True
        assert served.fetch(None, continuation_id) == _NOT_FOUND

    def test_an_anonymous_entry_is_not_an_authenticated_callers(self, monkeypatch, served, mint) -> None:
        _auth(monkeypatch, enabled=False)
        continuation_id = mint(None)

        _auth(monkeypatch, enabled=True)

        assert served.fetch(ALICE, continuation_id) == _NOT_FOUND
        assert served.delete(BOB, continuation_id)["deleted"] is False


# --- 4. no log line carries the id --------------------------------------------


class _Recorder:
    """A stand-in logger that keeps every line, at every level."""

    def __init__(self) -> None:
        self.lines: list[tuple[str, str, dict[str, Any]]] = []

    def __getattr__(self, level: str):
        def log(event: str, *_args: Any, **kwargs: Any) -> None:
            self.lines.append((level, event, kwargs))

        return log


@pytest.mark.usefixtures("backend", "auth_on")
class TestNoLogLineCarriesTheId:
    def test_at_any_level(self, monkeypatch, served, mint) -> None:
        recorder = _Recorder()
        for module in (
            manager_module,
            memory_cache_module,
            redis_cache_module,
            continuation_module,
            executor_module,
            batch,
        ):
            monkeypatch.setattr(module, "logger", recorder)

        continuation_id = mint(ALICE)
        served.fetch(ALICE, continuation_id)
        served.fetch(ALICE, continuation_id, offset=0, limit=10_000_000)  # over the maximum: warns
        served.fetch(BOB, continuation_id)
        served.delete(BOB, continuation_id)
        served.delete(ALICE, continuation_id)
        served.fetch(ALICE, continuation_id)

        logged = repr(recorder.lines)
        assert _suffix(continuation_id) not in logged
        assert continuation_id not in logged

        (truncated,) = [kwargs for level, event, kwargs in recorder.lines if event == "result_truncated"]
        assert truncated["call_index"] == 0
        assert truncated["batch_id"] == continuation_id.split("_")[1]


# --- 5. Redis keeps the owner with the value ----------------------------------


class TestRedisStoresTheOwnerWithTheValue:
    def test_the_value_names_the_owner(self, redis_backend, auth_on, served, mint) -> None:
        continuation_id = mint(ALICE)

        (stored,) = redis_backend.values.values()

        assert '"tenant_id":"A"' in stored and '"principal_id":"svc:alice"' in stored
        assert stored.endswith(_BIG_JSON), "the payload is stored as it was, after the owner"
        assert served.fetch(ALICE, continuation_id)["total_size_bytes"] == len(_BIG_JSON)

    def test_an_entry_stored_before_the_upgrade_is_anonymous(self, redis_backend, monkeypatch, served) -> None:
        """A value written by an older replica is the bare payload, with no owner.

        It is read as owned by no identity: an auth-off gateway keeps serving
        it, and an authenticated caller is told it does not exist. It expires
        within ``cache_ttl_s`` either way.
        """
        continuation_id = _random_id()
        redis_backend.values[f"mcp:cont:{continuation_id}"] = _BIG_JSON

        _auth(monkeypatch, enabled=True)
        assert served.fetch(ALICE, continuation_id) == _NOT_FOUND
        assert served.delete(ALICE, continuation_id)["deleted"] is False

        _auth(monkeypatch, enabled=False)
        assert served.fetch(None, continuation_id)["data"] == _BIG

    @pytest.mark.parametrize(
        "header",
        ["", "not json", "[]", '{"tenant_id": 7, "principal_id": null}', '{"tenant_id": "A"}'],
        ids=["empty", "not-json", "not-an-object", "wrong-type", "missing-field"],
    )
    def test_a_malformed_owner_is_not_found(self, redis_backend, monkeypatch, served, header: str) -> None:
        continuation_id = _random_id()
        redis_backend.values[f"mcp:cont:{continuation_id}"] = (
            redis_cache_module._OWNER_PREFIX + header + "\n" + _BIG_JSON
        )

        _auth(monkeypatch, enabled=False)

        assert served.fetch(None, continuation_id) == _NOT_FOUND
        assert served.delete(None, continuation_id)["deleted"] is False


# --- 6. what a log line may name ----------------------------------------------


class TestTheLogRef:
    # Imported here, not at the top, so this file still imports against a tree
    # without the fix and every other test shows what that tree does.
    def test_a_generated_id_is_named_without_its_suffix(self) -> None:
        from mcp_hangar.domain.value_objects.truncation import continuation_log_ref

        continuation_id = ContinuationId.generate("batch-1", 3)

        assert continuation_log_ref(continuation_id.value) == "cont_batch-1_3"
        assert _suffix(continuation_id.value) not in repr(continuation_id)

    @pytest.mark.parametrize("value", ["x", "_x", "nounderscore"])
    def test_a_string_of_another_shape_is_not_echoed(self, value: str) -> None:
        from mcp_hangar.domain.value_objects.truncation import continuation_log_ref

        # hangar_delete_continuation does not check the id's shape, so what a
        # caller sends reaches this too.
        assert continuation_log_ref(value) == "cont_?"
