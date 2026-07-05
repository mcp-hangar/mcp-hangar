"""Shared helpers for the T1 multi-backend group + canary live tests.

Kept in a plain module (not ``conftest.py``) so both the fixture and the tests
can import the ``GroupHarness``, the ``serving_member`` HTTP helper, and the
canary bucketing mirror without relying on conftest-symbol importability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import hashlib
import json
import sys

#: Identity-revealing stub backend (its ``whoami`` tool echoes the serving member).
IDENTITY_SERVER = Path(__file__).resolve().parents[2] / "examples" / "provider_identity" / "server.py"

GROUP_ID = "llm-group"
MEMBER_A = "member-a"
MEMBER_B = "member-b"
MEMBERS = (MEMBER_A, MEMBER_B)

#: Canary member (the "new version") and the % of tenants a split sends to it.
CANARY_MEMBER = MEMBER_B
SPLIT_PCT = 30
#: Explicit per-tenant pins (win over the split); one to each member.
PINNED = {"tenant:pin-a": MEMBER_A, "tenant:pin-b": MEMBER_B}
#: Distinct tenants used to measure the split ratio.
SPLIT_TENANTS = tuple(f"tenant:split:{i}" for i in range(150))

GROUP_CONFIG = """\
logging:
  level: WARNING
auth:
  enabled: true
  # Anonymous stays allowed so the group-invocation test needs no credentials;
  # a request that presents a seeded X-API-Key is authenticated as its tenant.
  allow_anonymous: true
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
mcp_servers:
  {group_id}:
    mode: group
    strategy: round_robin
    auto_start: true
    members:
      - id: {member_a}
        mode: subprocess
        command: ["{python}", "{server}", "{member_a}"]
        idle_ttl_s: 120
      - id: {member_b}
        mode: subprocess
        command: ["{python}", "{server}", "{member_b}"]
        idle_ttl_s: 120
    canary:
      member: {canary_member}
      split_pct: {split_pct}
      pinned_tenants:
        "tenant:pin-a": {member_a}
        "tenant:pin-b": {member_b}
"""


def render_config(auth_db: Path, identity_server: Path = IDENTITY_SERVER) -> str:
    """Render the group + canary + auth config with absolute paths."""
    return GROUP_CONFIG.format(
        auth_db=str(auth_db),
        group_id=GROUP_ID,
        member_a=MEMBER_A,
        member_b=MEMBER_B,
        canary_member=CANARY_MEMBER,
        split_pct=SPLIT_PCT,
        python=sys.executable,
        server=str(identity_server),
    )


def canary_bucket(tenant_id: str) -> int:
    """Mirror hangar's #283 bucketing: ``SHA-256(tenant_id) % 100``."""
    return int(hashlib.sha256(tenant_id.encode()).hexdigest(), 16) % 100


def expected_split_target(tenant_id: str) -> str | None:
    """The member a split (no pin) routes ``tenant_id`` to, or None for the LB."""
    return CANARY_MEMBER if canary_bucket(tenant_id) < SPLIT_PCT else None


@dataclass
class GroupHarness:
    """A live multi-backend group under test, plus its canary metadata."""

    base_url: str
    group_id: str = GROUP_ID
    members: tuple[str, ...] = MEMBERS
    canary_member: str = CANARY_MEMBER
    split_pct: int = SPLIT_PCT
    pinned: dict[str, str] = field(default_factory=lambda: dict(PINNED))
    #: tenant_id -> raw API key (each key authenticates as that tenant).
    tenant_keys: dict[str, str] = field(default_factory=dict)


def seed_tenant_keys(db_path: Path, tenants: list[str]) -> dict[str, str]:
    """Seed one API key per tenant into a SQLite key store; return {tenant: key}.

    Uses the shipped ``SQLiteApiKeyStore`` so the server (pointed at the same DB)
    authenticates each key as its seeded ``tenant_id``.
    """
    from mcp_hangar.auth.infrastructure.sqlite_store import SQLiteApiKeyStore

    store = SQLiteApiKeyStore(db_path)
    store.initialize()
    try:
        return {
            tenant: store.create_key(principal_id=f"svc:{tenant}", name=f"k-{i}", tenant_id=tenant)
            for i, tenant in enumerate(tenants)
        }
    finally:
        store.close()


def serving_member(harness: GroupHarness, tenant_id: str | None = None, tool: str = "whoami") -> str | None:
    """Invoke a group tool via ``hangar_call`` over HTTP; return the serving member.

    Tenant identity (when given) is carried on the shipped ``X-API-Key`` header,
    which authenticates as the tenant that key was seeded for. Returns the member
    id that served the call (``member-a``/``member-b``), or ``None`` if no member
    served it (e.g. the group has not warmed yet).
    """
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers: dict[str, str] = {}
    if tenant_id is not None:
        key = harness.tenant_keys.get(tenant_id)
        if key is None:
            raise KeyError(f"no seeded API key for tenant {tenant_id!r}")
        headers["X-API-Key"] = key

    async def _call() -> str | None:
        async with streamablehttp_client(f"{harness.base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "hangar_call",
                    {"calls": [{"mcp_server": harness.group_id, "tool": tool, "arguments": {}}]},
                )
                blob = json.dumps(result.model_dump(mode="json"), default=str)
                for member in harness.members:
                    if member in blob:
                        return member
                return None

    return asyncio.run(_call())
