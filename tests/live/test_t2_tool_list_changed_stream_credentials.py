"""Tier 2 live verification: a list_changed stream does not outlive its credential (#1366).

BLACK-BOX against a REAL ``mcp-hangar serve --http`` front door with API keys
in the shipped SQLite store and JWTs from an issuer served on loopback. The
stream authenticates once, as it opens, so the gateway has to end it when the
credential it opened with stops being valid:

* an API key revoked over ``DELETE /api/auth/keys/{id}`` ends its streams at
  once, and the reconnect is refused;
* an API key with an expiry ends its stream when that expiry passes;
* a JWT's stream ends when its ``exp`` passes, not an hour later.

Each is read back from ``GET /metrics``: ``mcp_hangar_tool_list_changed_streams_ended_total``
by reason. Skip-safe like the rest of the tier. Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest -m "live and t2" -o addopts="" \\
        tests/live/test_t2_tool_list_changed_stream_credentials.py
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.live.conftest import running_hangar
from tests.live.test_t2_session_suspension import _AUDIENCE, _Issuer

pytestmark = [pytest.mark.live, pytest.mark.t2]

_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: front_door
auth:
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
mcp_servers: {{}}
"""


@dataclass
class _Gateway:
    url: str
    operator: str
    agent: str
    agent_key_id: str
    issuer: _Issuer


@pytest.fixture(scope="module")
def gateway(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Gateway]:
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    issuer = _Issuer()
    workdir = tmp_path_factory.mktemp("list_changed_credentials")
    auth_db = workdir / "auth.db"
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        operator = store.create_key(principal_id="svc:operator", name="operator")
        agent = store.create_key(principal_id="svc:agent", name="agent", tenant_id="t1")
        agent_key_id = store.list_keys("svc:agent")[0].key_id
    finally:
        store.close()

    config = _CONFIG.format(auth_db=auth_db, issuer=issuer.issuer, audience=_AUDIENCE, jwks_uri=issuer.jwks_uri)
    try:
        with running_hangar(workdir, config) as hangar:
            yield _Gateway(hangar.base_url, operator, agent, agent_key_id, issuer)
    finally:
        issuer.close()


class _Stream:
    """A ``GET /mcp`` held open on its own thread, the way the TypeScript client holds it."""

    def __init__(self, url: str, credential: dict[str, str]) -> None:
        self.events: queue.Queue[str] = queue.Queue()
        headers = {"Accept": "text/event-stream", "MCP-Protocol-Version": "2025-11-25", **credential}
        threading.Thread(target=self._read, args=(url, headers), daemon=True).start()
        self.status = self.events.get(timeout=10)

    def _read(self, url: str, headers: dict[str, str]) -> None:
        try:
            with httpx.stream("GET", f"{url}/mcp", headers=headers, timeout=httpx.Timeout(10.0, read=None)) as response:
                self.events.put(str(response.status_code))
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        self.events.put(line)
        except httpx.HTTPError:
            pass
        finally:
            self.events.put("closed")

    def ended_within(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while (left := deadline - time.monotonic()) > 0:
            try:
                if self.events.get(timeout=left) == "closed":
                    return True
            except queue.Empty:
                return False
        return False


def _ended(url: str, reason: str) -> float:
    scrape = httpx.get(f"{url}/metrics", timeout=5.0).text
    sample = f'mcp_hangar_tool_list_changed_streams_ended_total{{reason="{reason}"}}'
    return sum(float(line.split()[-1]) for line in scrape.splitlines() if line.startswith(sample))


def test_revoking_an_api_key_ends_its_open_stream_and_refuses_the_reconnect(gateway: _Gateway) -> None:
    before = _ended(gateway.url, "credential_revoked")
    stream = _Stream(gateway.url, {"X-API-Key": gateway.agent})
    assert stream.status == "200"
    assert "list_changed" in stream.events.get(timeout=5)  # told once on open

    revoked = httpx.delete(
        f"{gateway.url}/api/auth/keys/{gateway.agent_key_id}", headers={"X-API-Key": gateway.operator}, timeout=5.0
    )
    assert revoked.status_code == 200, revoked.text[:300]

    assert stream.ended_within(5), "the stream outlived its revoked key"
    assert _ended(gateway.url, "credential_revoked") == before + 1
    assert _Stream(gateway.url, {"X-API-Key": gateway.agent}).status == "401"


def test_a_jwt_stream_ends_when_the_token_expires(gateway: _Gateway) -> None:
    before = _ended(gateway.url, "credential_expired")
    token = gateway.issuer.token(exp=int(time.time()) + 3)
    stream = _Stream(gateway.url, {"Authorization": f"Bearer {token}"})
    assert stream.status == "200"

    assert stream.ended_within(10), "the stream outlived its token"
    assert _ended(gateway.url, "credential_expired") == before + 1


def test_an_api_key_stream_ends_when_the_key_expires(gateway: _Gateway) -> None:
    before = _ended(gateway.url, "credential_expired")
    expires_at = datetime.now(UTC) + timedelta(seconds=6)
    created = httpx.post(
        f"{gateway.url}/api/auth/keys",
        headers={"X-API-Key": gateway.operator},
        json={"principal_id": "svc:short-lived", "name": "short-lived", "expires_at": expires_at.isoformat()},
        timeout=5.0,
    )
    assert created.status_code == 201, created.text[:300]
    key = created.json()["raw_key"]

    stream = _Stream(gateway.url, {"X-API-Key": key})
    assert stream.status == "200"
    assert "list_changed" in stream.events.get(timeout=5)

    assert stream.ended_within(10), "the stream outlived its expired API key"
    assert _ended(gateway.url, "credential_expired") == before + 1
    assert _Stream(gateway.url, {"X-API-Key": key}).status == "401"
