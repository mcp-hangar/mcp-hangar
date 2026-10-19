"""Tier 2 live verification: ``hangar_whoami`` over the real front door (#1363).

Black-box against REAL ``mcp-hangar serve --http`` processes, driven over
streamable-HTTP the way a client drives them. Identity over that transport is
read off ``request.state.auth`` in the SDK's per-session task, which is where it
has been lost before, so this is checked here rather than with a mock context.

With API-key auth on (``tool_access.mode: front_door``):

* a key's caller is offered ``hangar_whoami`` and is answered with that key's
  principal, tenant and role bindings;
* a call the reported roles do not admit is in fact refused, and one they admit
  is served;
* a key whose principal holds no role at all is still offered whoami, and only
  whoami, from the management surface.

With auth off, the tool is absent from the list and a call to it is -32601.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t2_whoami.py -m "live and t2" -o addopts="" -rs
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_TENANT = "tenant-who"
_VIEWER = "svc:who-viewer"
_NOBODY = "svc:who-nobody"

_AUTH_CONFIG = """\
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
  role_assignments:
    - principal: "{viewer}"
      role: viewer
      scope: global
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""

_NO_AUTH_CONFIG = """\
logging:
  level: WARNING
tool_access:
  mode: front_door
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    env:
      MCP_TRANSPORT: stdio
    idle_ttl_s: 60
"""


@dataclass
class _Session:
    """What one MCP session saw: the listed names and the outcome of each call."""

    listed: set[str]
    calls: dict[str, tuple[bool, str, Any]]


def _session(base_url: str, headers: dict[str, str], calls: list[str]) -> _Session:
    """List the tools, then call each of *calls* with no arguments, in one MCP session."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run() -> _Session:
        outcomes: dict[str, tuple[bool, str, Any]] = {}
        async with open_mcp_streams(f"{base_url}/mcp", headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = {tool.name for tool in (await session.list_tools()).tools}
                for name in calls:
                    try:
                        result = await session.call_tool(name, {})
                    except Exception as exc:  # noqa: BLE001 -- a protocol-level refusal is an outcome here
                        outcomes[name] = (True, str(exc), None)
                        continue
                    text = " ".join(getattr(block, "text", "") or "" for block in result.content)
                    structured = getattr(result, "structured_content", None) or getattr(
                        result, "structuredContent", None
                    )
                    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
                    outcomes[name] = (is_error, text, structured)
        return _Session(listed=listed, calls=outcomes)

    return asyncio.run(asyncio.wait_for(_run(), timeout=60))


def _report(outcome: tuple[bool, str, Any]) -> dict:
    is_error, text, structured = outcome
    assert not is_error, text
    for candidate in (structured, (structured or {}).get("result") if isinstance(structured, dict) else None):
        if isinstance(candidate, dict) and "principal" in candidate:
            return candidate
    return json.loads(text)


@pytest.fixture(scope="module")
def authed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, dict[str, str]]]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    workdir = tmp_path_factory.mktemp("whoami_authed")
    auth_db = workdir / "auth.db"
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        keys = {p: store.create_key(principal_id=p, name=p, tenant_id=_TENANT) for p in (_VIEWER, _NOBODY)}
    finally:
        store.close()
    config = _AUTH_CONFIG.format(auth_db=auth_db, viewer=_VIEWER, python=sys.executable, server=str(_MATH_SERVER))
    with running_hangar(workdir, config) as hangar:
        yield hangar.base_url, keys


@pytest.fixture(scope="module")
def unauthed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")
    workdir = tmp_path_factory.mktemp("whoami_unauthed")
    with running_hangar(workdir, _NO_AUTH_CONFIG.format(python=sys.executable, server=str(_MATH_SERVER))) as hangar:
        yield hangar.base_url


def test_whoami_names_the_keys_principal_and_its_roles(authed) -> None:
    base_url, keys = authed

    seen = _session(base_url, {"X-API-Key": keys[_VIEWER]}, ["hangar_whoami"])

    assert "hangar_whoami" in seen.listed, seen.listed
    report = _report(seen.calls["hangar_whoami"])
    assert report["principal"]["id"] == _VIEWER
    assert report["tenant_id"] == _TENANT
    assert report["roles"] == [{"role": "viewer", "scope": "global"}]


def test_what_the_reported_roles_deny_is_denied(authed) -> None:
    """viewer holds mcp_servers:read, not :lifecycle: hangar_list is served and hangar_stop refused."""
    base_url, keys = authed

    seen = _session(base_url, {"X-API-Key": keys[_VIEWER]}, ["hangar_whoami", "hangar_list", "hangar_stop"])

    from mcp_hangar.auth.roles import BUILTIN_ROLES

    [role] = [BUILTIN_ROLES[b["role"]] for b in _report(seen.calls["hangar_whoami"])["roles"]]
    assert role.has_permission("mcp_servers", "read", "*")
    assert not role.has_permission("mcp_servers", "lifecycle", "*")

    assert "hangar_list" in seen.listed and "hangar_stop" not in seen.listed, seen.listed
    assert not seen.calls["hangar_list"][0], seen.calls["hangar_list"]
    is_error, text, _ = seen.calls["hangar_stop"]
    assert is_error and "not found" in text.lower(), text


def test_a_caller_with_no_role_is_offered_whoami_and_nothing_else(authed) -> None:
    base_url, keys = authed

    seen = _session(base_url, {"X-API-Key": keys[_NOBODY]}, ["hangar_whoami"])

    assert {name for name in seen.listed if name.startswith("hangar_")} == {"hangar_whoami"}, seen.listed
    report = _report(seen.calls["hangar_whoami"])
    assert report["principal"]["id"] == _NOBODY
    assert report["roles"] == []


def test_with_auth_off_whoami_is_absent(unauthed) -> None:
    seen = _session(unauthed, {}, ["hangar_whoami"])

    assert "hangar_whoami" not in seen.listed, seen.listed
    is_error, text, _ = seen.calls["hangar_whoami"]
    assert is_error and "not found" in text.lower(), text
