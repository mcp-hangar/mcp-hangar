"""Tier 2 live verification: a suspended session is refused (GHSA-fhwh-fmq2-7m5c).

Black-box against a REAL ``mcp-hangar serve --http``, over real streamable-HTTP,
because this is a per-request control and those have failed open here before
when tested only with a mock context. Auth is on: API keys from a seeded SQLite
store, and OIDC against a JWKS endpoint this module serves on loopback, so a
bearer token's ``sid`` claim is verified exactly as a real IdP's would be.

The operator suspends with ``POST /api/sessions/{id}/suspend`` and lifts with
``DELETE``; the agent calls tools carrying that session id. Claims:

* a tool call carrying a suspended session id is refused -- ``hangar_call``,
  a ``hangar_*`` management tool, both continuation tools, and a front door's
  flat tool -- and is served again once the suspension is lifted;
* a verified token's ``sid`` is refused, and an ``x-session-id`` header cannot
  replace it;
* ``x-session-id`` is honoured only from a trusted proxy: with
  ``MCP_TRUSTED_PROXIES`` not covering the client, the same header names a
  suspended session and the call is served;
* a caller carrying no session id is served (the documented limit);
* ``tasks/get`` and ``tasks/cancel`` are refused, and on a front door so are
  ``prompts/get``, ``resources/read`` and ``completion/complete``, with the
  JSON-RPC refusal those methods carry;
* the peer is the address that connected: a loopback proxy that also sends
  ``X-Forwarded-For`` is still trusted, and the auth lockout counts against the
  address a trusted proxy saw -- or, from an untrusted peer, against the peer,
  whatever ``X-Forwarded-For`` it sends.

Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t2" -o addopts=""
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

import httpx
import pytest

from tests.live.conftest import _MATH_SERVER, RunningHangar, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_AUDIENCE = "mcp-hangar-live"
_KID = "live-session-suspension"
_REFUSAL = "Session suspended"

_CONFIG = """\
logging:
  level: WARNING
{topology}auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  oidc:
    enabled: true
    issuer: "{issuer}"
    audience: "{audience}"
    jwks_uri: "{jwks_uri}"
  role_assignments:
    - principal: "svc:operator"
      role: admin
      scope: global
    - principal: "svc:agent"
      role: developer
      scope: global
    - principal: "user:jwt-agent"
      role: developer
      scope: global
{rate_limit}mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""


class _Issuer:
    """A token issuer: an RSA key, and its JWKS served on loopback."""

    def __init__(self) -> None:
        jwt = pytest.importorskip("jwt")
        rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

        self._jwt = jwt
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key()))
        jwk.update(kid=_KID, use="sig", alg="RS256")
        body = json.dumps({"keys": [jwk]}).encode()

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 -- the stdlib's name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.issuer = f"http://127.0.0.1:{self._server.server_address[1]}"
        self.jwks_uri = f"{self.issuer}/jwks.json"

    def token(self, **claims: object) -> str:
        now = int(time.time())
        payload = {"iss": self.issuer, "aud": _AUDIENCE, "sub": "user:jwt-agent", "iat": now, "exp": now + 600}
        payload.update(claims)
        return self._jwt.encode(payload, self._key, algorithm="RS256", headers={"kid": _KID})

    def close(self) -> None:
        self._server.shutdown()


@dataclass
class _Gateway:
    hangar: RunningHangar
    operator: str
    agent: str
    issuer: _Issuer

    @property
    def url(self) -> str:
        return self.hangar.base_url


@pytest.fixture(scope="module")
def issuer() -> Iterator[_Issuer]:
    served = _Issuer()
    yield served
    served.close()


#: A lockout three failures deep, so the address it counts against is observable.
_LOCKING = "  rate_limit:\n    max_attempts: 3\n    window_seconds: 60\n    lockout_seconds: 300\n"


def _gateway(
    workdir: Path, issuer: _Issuer, *, trusted_proxies: str, front_door: bool = False, locking: bool = False
) -> Iterator[_Gateway]:
    """Seed two keys, write the config, and run a hangar with *trusted_proxies*."""
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    auth_db = workdir / "auth.db"
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        operator = store.create_key(principal_id="svc:operator", name="operator")
        agent = store.create_key(principal_id="svc:agent", name="agent", tenant_id="t1")
    finally:
        store.close()

    config = _CONFIG.format(
        topology="tool_access:\n  mode: front_door\n" if front_door else "",
        rate_limit=_LOCKING if locking else "",
        auth_db=auth_db,
        issuer=issuer.issuer,
        audience=_AUDIENCE,
        jwks_uri=issuer.jwks_uri,
        python=sys.executable,
        server=str(_MATH_SERVER),
    )
    env = {**os.environ, "MCP_TRUSTED_PROXIES": trusted_proxies}
    with running_hangar(workdir, config, env=env) as hangar:
        yield _Gateway(hangar=hangar, operator=operator, agent=agent, issuer=issuer)


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory, issuer: _Issuer) -> Iterator[_Gateway]:
    """The test client is on loopback, which the default trusted-proxy list names."""
    yield from _gateway(tmp_path_factory.mktemp("suspension"), issuer, trusted_proxies="127.0.0.1,::1")


@pytest.fixture(scope="module")
def untrusting_gateway(tmp_path_factory: pytest.TempPathFactory, issuer: _Issuer) -> Iterator[_Gateway]:
    """``MCP_TRUSTED_PROXIES`` names some other host, so the loopback client is untrusted."""
    yield from _gateway(tmp_path_factory.mktemp("suspension_untrusted"), issuer, trusted_proxies="203.0.113.7")


@pytest.fixture(scope="module")
def front_door_gateway(tmp_path_factory: pytest.TempPathFactory, issuer: _Issuer) -> Iterator[_Gateway]:
    yield from _gateway(
        tmp_path_factory.mktemp("suspension_front_door"), issuer, trusted_proxies="127.0.0.1,::1", front_door=True
    )


@pytest.fixture(scope="module")
def locking_gateway(tmp_path_factory: pytest.TempPathFactory, issuer: _Issuer) -> Iterator[_Gateway]:
    """Loopback is a trusted proxy here, and three failures lock an address out."""
    yield from _gateway(tmp_path_factory.mktemp("lockout"), issuer, trusted_proxies="127.0.0.1,::1", locking=True)


@pytest.fixture(scope="module")
def locking_untrusting_gateway(tmp_path_factory: pytest.TempPathFactory, issuer: _Issuer) -> Iterator[_Gateway]:
    """Loopback is NOT a trusted proxy here, and three failures lock an address out."""
    yield from _gateway(
        tmp_path_factory.mktemp("lockout_untrusted"), issuer, trusted_proxies="203.0.113.7", locking=True
    )


# --- driving it ---------------------------------------------------------------


def _session() -> str:
    return f"live-{uuid.uuid4().hex[:16]}"


def _suspend(gw: _Gateway, session_id: str) -> None:
    resp = httpx.post(
        f"{gw.url}/api/sessions/{session_id}/suspend",
        headers={"X-API-Key": gw.operator},
        json={"reason": "live verification"},
        timeout=10.0,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"session_id": session_id, "suspended": True}


def _lift(gw: _Gateway, session_id: str) -> None:
    resp = httpx.delete(f"{gw.url}/api/sessions/{session_id}/suspend", headers={"X-API-Key": gw.operator}, timeout=10.0)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"session_id": session_id, "suspended": False}


def _as_agent(gw: _Gateway, session_id: str | None = None) -> dict[str, str]:
    headers = {"X-API-Key": gw.agent}
    if session_id is not None:
        headers["x-session-id"] = session_id
    return headers


def _as_token(gw: _Gateway, sid: str, header_session: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {gw.issuer.token(sid=sid, tenant_id='t1')}"}
    if header_session is not None:
        headers["x-session-id"] = header_session
    return headers


def _mcp(url: str, headers: dict[str, str], tool: str | None, arguments: dict | None = None) -> tuple[bool, str]:
    """Call *tool* over streamable-HTTP, or list tools when *tool* is None: ``(is_error, text)``."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run():
        async with open_mcp_streams(f"{url}/mcp", headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                if tool is None:
                    return await session.list_tools()
                return await session.call_tool(tool, arguments or {})

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 -- a protocol-level refusal is an outcome here
        return True, str(exc)
    if tool is None:
        return False, " ".join(t.name for t in result.tools)
    text = " ".join(getattr(block, "text", "") or "" for block in getattr(result, "content", None) or [])
    return bool(getattr(result, "is_error", None) or getattr(result, "isError", None)), text


def _leaf(exc: BaseException) -> BaseException:
    """The error itself, out of the task groups the client's transport wraps it in."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _call(url: str, headers: dict[str, str], use) -> tuple[bool, str]:
    """Run ``use(session)`` over streamable-HTTP: ``(raised, text)``.

    For methods that answer with a JSON-RPC error rather than a tool error:
    the text carries the error's code, message and data.
    """
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run():
        async with open_mcp_streams(f"{url}/mcp", headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await use(session)

    try:
        result = asyncio.run(_run())
    except BaseException as exc:  # noqa: BLE001 -- a JSON-RPC refusal is an outcome here
        leaf = _leaf(exc)
        error = getattr(leaf, "error", None) or leaf
        return True, f"{getattr(error, 'code', '')} {leaf} {getattr(error, 'data', '')}"
    return False, repr(result)


def _task_request(method: str, task_id: str) -> object:
    """A ``tasks/*`` request, which the client has no typed helper for.

    The params must be a typed model: a plain dict handed to ``Request`` is
    dropped on the way to the wire, and the relay then answers ``-32602`` for
    the missing ``taskId`` before any handler runs -- which would make every
    assertion here about the wrong thing.
    """

    async def use(session):
        from mcp_types import Request, RequestParams
        from pydantic import TypeAdapter

        class _TaskParams(RequestParams):
            task_id: str

        request = Request[_TaskParams, str](method=method, params=_TaskParams(task_id=task_id))
        return await session.send_request(request, TypeAdapter(dict))

    return use


def _assert_not_suspension(outcome: tuple[bool, str]) -> None:
    assert _REFUSAL not in outcome[1], outcome


def _assert_suspension_error(outcome: tuple[bool, str]) -> None:
    raised, text = outcome
    assert raised, text
    assert _REFUSAL in text and "session_suspended" in text, text


def _add(gw: _Gateway, headers: dict[str, str]) -> tuple[bool, str]:
    return _mcp(
        gw.url,
        headers,
        "hangar_call",
        {"calls": [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]},
    )


def _assert_served_add(outcome: tuple[bool, str]) -> None:
    is_error, text = outcome
    assert not is_error, text
    assert _REFUSAL not in text, text
    assert json.loads(text)["success"] is True, text


def _assert_refused(outcome: tuple[bool, str]) -> None:
    is_error, text = outcome
    assert is_error, text
    assert _REFUSAL in text, text


# --- claims ---------------------------------------------------------------------


def test_a_suspended_session_is_refused_on_every_egress_path_and_served_once_lifted(gateway: _Gateway) -> None:
    session_id = _session()
    headers = _as_agent(gateway, session_id)
    _assert_served_add(_add(gateway, headers))

    _suspend(gateway, session_id)

    _assert_refused(_add(gateway, headers))
    _assert_refused(_mcp(gateway.url, headers, "hangar_list"))
    _assert_refused(_mcp(gateway.url, headers, "hangar_fetch_continuation", {"continuation_id": "cont_live"}))
    _assert_refused(_mcp(gateway.url, headers, "hangar_delete_continuation", {"continuation_id": "cont_live"}))
    assert "session_suspended_call_refused" in gateway.hangar.output()

    # The same credential on another session, and on none, is not refused: a
    # suspension names a session, not a principal.
    _assert_served_add(_add(gateway, _as_agent(gateway, _session())))
    _assert_served_add(_add(gateway, _as_agent(gateway)))

    _lift(gateway, session_id)

    _assert_served_add(_add(gateway, headers))
    is_error, text = _mcp(gateway.url, headers, "hangar_fetch_continuation", {"continuation_id": "cont_live"})
    assert not is_error and _REFUSAL not in text, text


def test_a_tokens_sid_is_refused_and_a_header_cannot_replace_it(gateway: _Gateway) -> None:
    sid = _session()
    _assert_served_add(_add(gateway, _as_token(gateway, sid)))

    _suspend(gateway, sid)

    _assert_refused(_add(gateway, _as_token(gateway, sid)))
    _assert_refused(_add(gateway, _as_token(gateway, sid, header_session=_session())))

    _lift(gateway, sid)

    _assert_served_add(_add(gateway, _as_token(gateway, sid)))


def test_an_untrusted_clients_header_is_not_honoured(untrusting_gateway: _Gateway) -> None:
    # The client is on loopback and MCP_TRUSTED_PROXIES names another host, so
    # its x-session-id is not a session id this gateway believes.
    session_id = _session()
    _suspend(untrusting_gateway, session_id)

    _assert_served_add(_add(untrusting_gateway, _as_agent(untrusting_gateway, session_id)))

    # A verified token does not depend on proxy trust.
    sid = _session()
    _suspend(untrusting_gateway, sid)
    _assert_refused(_add(untrusting_gateway, _as_token(untrusting_gateway, sid)))


def test_a_loopback_proxy_that_forwards_an_address_is_still_the_peer(gateway: _Gateway) -> None:
    # uvicorn used to replace a loopback peer with its X-Forwarded-For address,
    # so a loopback proxy sending both headers was not seen as a proxy and its
    # x-session-id was ignored. With that handling off, it is the peer.
    session_id = _session()
    _suspend(gateway, session_id)

    forwarded = {**_as_agent(gateway, session_id), "X-Forwarded-For": "198.51.100.4"}
    _assert_refused(_add(gateway, forwarded))
    _assert_refused(_add(gateway, _as_agent(gateway, session_id)))

    _lift(gateway, session_id)
    _assert_served_add(_add(gateway, forwarded))


def test_task_polling_and_cancelling_are_refused(gateway: _Gateway) -> None:
    # No real task: what matters is where the answer comes from. Served, the
    # relay's own ladder answers (unknown task, or a capability the client did
    # not declare); suspended, the refusal comes first.
    session_id = _session()
    headers = _as_agent(gateway, session_id)
    calls = [_task_request("tasks/get", "task-live"), _task_request("tasks/cancel", "task-live")]
    for use in calls:
        outcome = _call(gateway.url, headers, use)
        _assert_not_suspension(outcome)
        # The handler ran: the answer is the relay's own, not a params rejection.
        assert "Invalid request parameters" not in outcome[1], outcome

    _suspend(gateway, session_id)
    for use in calls:
        _assert_suspension_error(_call(gateway.url, headers, use))

    _lift(gateway, session_id)
    for use in calls:
        _assert_not_suspension(_call(gateway.url, headers, use))


def test_prompts_resources_and_completions_are_refused_on_a_front_door(front_door_gateway: _Gateway) -> None:
    from mcp_types import PromptReference

    session_id = _session()
    headers = _as_agent(front_door_gateway, session_id)

    async def prompt(session):
        return await session.get_prompt("no-such-prompt")

    async def resource(session):
        return await session.read_resource("hangar://math/no-such-resource")

    async def completion(session):
        return await session.complete(
            PromptReference(type="ref/prompt", name="no-such-prompt"), {"name": "a", "value": "b"}
        )

    uses = [prompt, resource, completion]
    for use in uses:
        _assert_not_suspension(_call(front_door_gateway.url, headers, use))

    _suspend(front_door_gateway, session_id)
    for use in uses:
        _assert_suspension_error(_call(front_door_gateway.url, headers, use))

    _lift(front_door_gateway, session_id)
    for use in uses:
        _assert_not_suspension(_call(front_door_gateway.url, headers, use))


_LOCKED = "Too many auth attempts"


def _me(gw: _Gateway, key: str, forwarded: str | None = None) -> httpx.Response:
    headers = {"X-API-Key": key}
    if forwarded is not None:
        headers["X-Forwarded-For"] = forwarded
    return httpx.get(f"{gw.url}/api/system/me", headers=headers, timeout=10.0)


def test_the_lockout_counts_against_the_address_a_trusted_proxy_saw(locking_gateway: _Gateway) -> None:
    # A loopback proxy appends what it saw to whatever the client wrote. The
    # client rotates what it writes; the lockout still lands on the address the
    # proxy saw, and nowhere else.
    for spoof in ("10.1.1.1", "10.2.2.2", "10.3.3.3"):
        assert _me(locking_gateway, "not-a-key", f"{spoof}, 198.51.100.7").status_code == 401

    assert _LOCKED in _me(locking_gateway, "not-a-key", "10.4.4.4, 198.51.100.7").text
    neighbour = _me(locking_gateway, "not-a-key", "198.51.100.8")
    assert neighbour.status_code == 401 and _LOCKED not in neighbour.text, neighbour.text
    assert _me(locking_gateway, locking_gateway.agent).status_code == 200  # the proxy itself is not locked out


def test_an_untrusted_peer_cannot_rotate_its_way_out_of_the_lockout(locking_untrusting_gateway: _Gateway) -> None:
    # Loopback is not a trusted proxy here, so X-Forwarded-For is ignored and
    # every attempt counts against the peer. With uvicorn rewriting the peer,
    # each new header value was a new address, and the lockout never landed.
    gw = locking_untrusting_gateway
    for forwarded in ("198.51.100.1", "198.51.100.2", "198.51.100.3"):
        assert _me(gw, "not-a-key", forwarded).status_code == 401

    assert _LOCKED in _me(gw, "not-a-key", "198.51.100.4").text


def test_a_suspended_session_is_refused_on_the_flat_surface(front_door_gateway: _Gateway) -> None:
    session_id = _session()
    headers = _as_agent(front_door_gateway, session_id)

    deadline = time.monotonic() + 20.0
    listed = ""
    while time.monotonic() < deadline:
        _is_error, listed = _mcp(front_door_gateway.url, headers, None)
        if "add" in listed.split():
            break
        time.sleep(0.5)
    assert "add" in listed.split(), f"the front door never projected math's add: {listed!r}"

    served = _mcp(front_door_gateway.url, headers, "add", {"a": 1, "b": 2})
    assert not served[0] and _REFUSAL not in served[1], served

    _suspend(front_door_gateway, session_id)
    _assert_refused(_mcp(front_door_gateway.url, headers, "add", {"a": 1, "b": 2}))

    _lift(front_door_gateway, session_id)
    served = _mcp(front_door_gateway.url, headers, "add", {"a": 1, "b": 2})
    assert not served[0] and _REFUSAL not in served[1], served
