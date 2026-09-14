"""Tier 2 live verification: a continuation answers only the caller that made the call.

Black-box against a REAL ``mcp-hangar serve --http`` with
truncation on and API-key auth (``allow_anonymous: false``). Keys are seeded
into its SQLite store carrying a tenant. Each key is a developer within that
tenant. Over streamable-HTTP:

* tenant A's ``hangar_call`` is truncated and hands back a continuation id;
* tenant B's fetch and delete of that id get exactly the answer a nonexistent
  id gets, and so does another principal in tenant A;
* the caller that made the call fetches it whole and in pages, then deletes it;
* the server's output never carries the id's random suffix.

Identity has been lost on this transport before, so the owner binding is
checked here rather than only with a mock context.

Run with::

    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live/test_t2_continuation_owner.py -m "live and t2" -o addopts=""
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sys
import uuid

import pytest

from tests.live.conftest import _MATH_SERVER, running_hangar

pytestmark = [pytest.mark.live, pytest.mark.t2]

_NOT_FOUND = {"found": False, "error": "Continuation not found (may have expired)"}

_CONFIG = """\
logging:
  level: INFO
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
    - principal: "svc:dev-a"
      role: developer
      scope: "tenant:A"
    - principal: "svc:dev-a2"
      role: developer
      scope: "tenant:A"
    - principal: "svc:dev-b"
      role: developer
      scope: "tenant:B"
truncation:
  enabled: true
  max_batch_size_bytes: 40
  min_per_response_bytes: 10
  cache_ttl_s: 300
mcp_servers:
  math:
    mode: subprocess
    command: ["{python}", "{server}"]
    idle_ttl_s: 60
"""

_KEY_TENANTS = {"dev-a": "A", "dev-a2": "A", "dev-b": "B"}


@dataclass
class _Harness:
    base_url: str
    keys: dict[str, str]
    log_path: Path


@pytest.fixture(scope="module")
def truncating_hangar(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Harness]:
    if not _MATH_SERVER.exists():
        pytest.skip(f"stub backend not found at {_MATH_SERVER}")

    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    workdir = tmp_path_factory.mktemp("hangar_continuation_owner")
    auth_db = workdir / "auth.db"
    store = SQLiteApiKeyStore(auth_db)
    store.initialize()
    try:
        keys = {
            name: store.create_key(principal_id=f"svc:{name}", name=name, tenant_id=tenant)
            for name, tenant in _KEY_TENANTS.items()
        }
    finally:
        store.close()

    config = _CONFIG.format(auth_db=auth_db, python=sys.executable, server=str(_MATH_SERVER))
    with running_hangar(workdir, config) as hangar:
        yield _Harness(base_url=hangar.base_url, keys=keys, log_path=hangar.log_path)


def _call(harness: _Harness, key: str, tool: str, arguments: dict) -> tuple[str, dict]:
    """Call *tool* over streamable-HTTP as *key*: the raw text and the parsed answer."""
    from mcp import ClientSession

    from tests.live._mcp_client import open_mcp_streams

    async def _run():
        async with open_mcp_streams(f"{harness.base_url}/mcp", {"X-API-Key": harness.keys[key]}) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(tool, arguments)

    result = asyncio.run(_run())
    text = " ".join(getattr(block, "text", "") or "" for block in getattr(result, "content", None) or [])
    structured = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    for candidate in (structured, (structured or {}).get("result") if isinstance(structured, dict) else None):
        if isinstance(candidate, dict) and ("found" in candidate or "deleted" in candidate or "results" in candidate):
            return text, candidate
    return text, json.loads(text)


def _truncated_call(harness: _Harness, key: str) -> str:
    """One ``math.add`` through ``hangar_call``, truncated; the continuation id it hands back."""
    _, batch = _call(
        harness, key, "hangar_call", {"calls": [{"mcp_server": "math", "tool": "add", "arguments": {"a": 1, "b": 2}}]}
    )
    (result,) = batch["results"]
    assert result.get("truncated") is True and result.get("continuation_id"), batch
    return str(result["continuation_id"])


def _random_id() -> str:
    return f"cont_{uuid.uuid4()}_0_{secrets.token_hex(4)}"


@pytest.mark.parametrize("intruder", ["dev-b", "dev-a2"], ids=["another-tenant", "same-tenant-another-principal"])
def test_another_caller_is_told_the_id_does_not_exist(truncating_hangar: _Harness, intruder: str) -> None:
    continuation_id = _truncated_call(truncating_hangar, "dev-a")

    text, answer = _call(truncating_hangar, intruder, "hangar_fetch_continuation", {"continuation_id": continuation_id})
    random_text, _ = _call(truncating_hangar, intruder, "hangar_fetch_continuation", {"continuation_id": _random_id()})
    _, deleted = _call(truncating_hangar, intruder, "hangar_delete_continuation", {"continuation_id": continuation_id})

    assert answer == _NOT_FOUND, answer
    assert text == random_text
    assert deleted == {"deleted": False, "continuation_id": continuation_id}
    _, owner = _call(truncating_hangar, "dev-a", "hangar_fetch_continuation", {"continuation_id": continuation_id})
    assert owner["found"] is True, "the intruder's delete removed the entry"


def test_the_caller_that_made_the_call_fetches_pages_and_deletes(truncating_hangar: _Harness) -> None:
    continuation_id = _truncated_call(truncating_hangar, "dev-a")

    _, whole = _call(truncating_hangar, "dev-a", "hangar_fetch_continuation", {"continuation_id": continuation_id})
    assert whole["found"] is True and whole["complete"] is True, whole

    chunks: list[str] = []
    offset = 0
    while True:
        _, page = _call(
            truncating_hangar,
            "dev-a",
            "hangar_fetch_continuation",
            {"continuation_id": continuation_id, "offset": offset, "limit": 16},
        )
        assert page["found"] is True, page
        chunks.append(page["data"])
        offset += len(page["data"].encode("utf-8"))
        if not page["has_more"]:
            break
    assert len(chunks) > 1
    assert offset == whole["total_size_bytes"]
    assert json.loads("".join(chunks)) == whole["data"]

    _, deleted = _call(truncating_hangar, "dev-a", "hangar_delete_continuation", {"continuation_id": continuation_id})
    _, gone = _call(truncating_hangar, "dev-a", "hangar_fetch_continuation", {"continuation_id": continuation_id})
    assert deleted == {"deleted": True, "continuation_id": continuation_id}
    assert gone == _NOT_FOUND


def test_the_server_output_never_carries_the_id(truncating_hangar: _Harness) -> None:
    continuation_id = _truncated_call(truncating_hangar, "dev-a")
    _call(truncating_hangar, "dev-a", "hangar_fetch_continuation", {"continuation_id": continuation_id})

    output = truncating_hangar.log_path.read_text(errors="replace")

    assert "result_truncated" in output, "the INFO line this checks was not emitted"
    assert continuation_id.rsplit("_", 1)[1] not in output
