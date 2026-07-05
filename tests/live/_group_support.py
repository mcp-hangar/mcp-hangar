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
import os
import sys

# The group + canary sweep issues a burst of ``hangar_call`` invocations (warm +
# pin probes + a per-tenant determinism loop) far above the command-bus rate
# limiter's default 10 rps / burst 20. That limiter is a single per-command-type
# token bucket configured ONLY via env (no config.yaml knob), so without headroom
# it rejects mid-sweep; those RateLimitExceeded rejections then feed the group's
# circuit breaker (opens after 10 failures, 60 s reset), after which every call
# fast-fails NoAvailableMemberError and the canary claim cannot be observed. The
# hangar subprocess started by the live fixture inherits THIS process's env
# (conftest's ``Popen`` passes no explicit ``env=``), so raise the ceiling here.
# ``setdefault`` keeps any operator/CI override; the limit is only ever raised, so
# no throttling behaviour is under test here.
os.environ.setdefault("MCP_RATE_LIMIT_RPS", "1000")
os.environ.setdefault("MCP_RATE_LIMIT_BURST", "2000")

#: Identity-revealing stub backend (its ``whoami`` tool echoes the serving member).
IDENTITY_SERVER = Path(__file__).resolve().parents[2] / "examples" / "provider_identity" / "server.py"

GROUP_ID = "llm-group"
MEMBER_A = "member-a"
MEMBER_B = "member-b"
MEMBERS = (MEMBER_A, MEMBER_B)

#: Shared group stamped onto every seeded tenant key. Since #389 the invoke path
#: enforces ``tool:invoke``; a role-less caller is denied ``roles_checked=[]`` and
#: the group never warms. One ``group:`` role assignment (below) grants the whole
#: fleet the built-in ``service-account`` role (which carries ``tool:invoke``),
#: keeping each key's per-tenant ``tenant_id`` intact for canary routing.
SVC_GROUP = "svc-callers"

#: Sentinel key in ``GroupHarness.tenant_keys`` for the tenant-less caller used by
#: the fixture's warm probe and the group-invocation test. It carries the shared
#: ``SVC_GROUP`` group (so it holds ``tool:invoke`` post-#389) but NO ``tenant_id``
#: -- with no tenant to pin/split on, canary routing falls through to the group's
#: round-robin, so a credentialled-but-tenant-agnostic caller can observe member
#: selection (anonymous can't: the invoke path hard-denies it since #389).
WARM_KEY = "__warm__"

#: A group member cold-starts on first use, so its very first ``hangar_call`` can
#: miss (no member echoed) before the subprocess is ready. ``hangar_call``'s own
#: ``max_attempts`` (single-flight cold-start retry) absorbs this server-side; a
#: small client-side reconnect retry covers transport-level transients. Because
#: canary routing is deterministic per tenant, retrying yields the SAME correct
#: member once warm -- it never changes which member is selected.
_SERVE_MAX_ATTEMPTS = 5  # hangar_call server-side attempts (single-flight cold start)
_SERVE_ATTEMPTS = 5  # client-side reconnect attempts (transport transients)
_SERVE_RETRY_DELAY_S = 0.5

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
  # Anonymous is allowed for the unauthenticated surfaces (health, MCP session
  # handshake), but every hangar_call in these tests presents a seeded X-API-Key:
  # since #389 the invoke path hard-denies an anonymous principal, so a credential
  # is required to reach member selection.
  allow_anonymous: true
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: sqlite
    path: {auth_db}
  # Since #389 the invoke path enforces tool:invoke, so callers must hold a role
  # that grants it or they are denied roles_checked=[] and the group never warms.
  # Grant the built-in service-account role (read + tool:invoke) to every seeded
  # key via the shared {svc_group} group. Per-key tenant_id is untouched, so
  # canary routing still keys off it; the tenant-less warm/round-robin key shares
  # the group and thus the same role.
  role_assignments:
    - principal: "group:{svc_group}"
      role: service-account
      scope: global
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
        svc_group=SVC_GROUP,
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
        keys = {
            tenant: store.create_key(
                principal_id=f"svc:{tenant}",
                name=f"k-{i}",
                tenant_id=tenant,
                groups=frozenset({SVC_GROUP}),
            )
            for i, tenant in enumerate(tenants)
        }
        # A tenant-less warm/round-robin key: same group (so it holds tool:invoke),
        # no tenant_id (so canary routing falls through to round-robin). Used by the
        # anonymous-free warm probe and the group-invocation test.
        keys[WARM_KEY] = store.create_key(
            principal_id="svc:warm",
            name="k-warm",
            groups=frozenset({SVC_GROUP}),
        )
        return keys
    finally:
        store.close()


def serving_member(harness: GroupHarness, tenant_id: str | None = None, tool: str = "whoami") -> str | None:
    """Invoke a group tool via ``hangar_call`` over HTTP; return the serving member.

    Tenant identity (when given) is carried on the shipped ``X-API-Key`` header,
    which authenticates as the tenant that key was seeded for. When no tenant is
    given, the tenant-less warm/round-robin key (``WARM_KEY``) is used instead:
    since #389 the invoke path hard-denies an anonymous principal, so even the
    tenant-agnostic caller must present a credential to reach member selection.
    Returns the member id that served the call (``member-a``/``member-b``), or
    ``None`` if no member served it after a bounded cold-start retry.
    """
    import asyncio
    import time

    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers: dict[str, str] = {}
    lookup = tenant_id if tenant_id is not None else WARM_KEY
    key = harness.tenant_keys.get(lookup)
    if key is None:
        raise KeyError(f"no seeded API key for {lookup!r}")
    headers["X-API-Key"] = key

    async def _call() -> str | None:
        async with streamablehttp_client(f"{harness.base_url}/mcp", headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "hangar_call",
                    {
                        "calls": [{"mcp_server": harness.group_id, "tool": tool, "arguments": {}}],
                        "max_attempts": _SERVE_MAX_ATTEMPTS,
                    },
                )
                blob = json.dumps(result.model_dump(mode="json"), default=str)
                for member in harness.members:
                    if member in blob:
                        return member
                return None

    # Bounded retry to absorb a transient cold-start miss; deterministic routing
    # means every successful attempt for a given tenant resolves the same member.
    served: str | None = None
    for attempt in range(_SERVE_ATTEMPTS):
        try:
            served = asyncio.run(_call())
        except Exception:  # noqa: BLE001 -- transient during member cold-start; retry
            served = None
        if served is not None:
            return served
        if attempt < _SERVE_ATTEMPTS - 1:
            time.sleep(_SERVE_RETRY_DELAY_S)
    return served
