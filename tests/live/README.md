# Live verification (`tests/live/`)

Black-box checks that features declared **production-ready** actually behave as
claimed when hangar is driven the way a real client/operator drives it — over
the shipped CLI, HTTP surface, and MCP protocol — not via internal Python APIs.

These are **opt-in** and **not a per-PR gate**. A fixture that cannot meet its
prerequisites *skips* (it never fails the suite), so this is safe to run
anywhere. They run on demand via the `live-verify` workflow (manual + nightly).

## Tiers

| Tier | Scope | Prerequisite |
|------|-------|--------------|
| **T0** | single process + a stub backend (`examples/provider_math`) — operational surface, `hangar_call`, lifecycle, tool-access policy, withdrawal/digest-pin, truncation/continuation | `mcp-hangar` on PATH (i.e. `uv run`) |
| **T1** | multi-backend / groups — group invocation, canary/failover, discovery, load-balancing | Docker + compose (≥2 backends) |
| **T2** | auth / IdP — JWT/OIDC, multi-issuer, RFC 8707 audience binding, `front_door` DENY, RBAC | Keycloak (`examples/auth-keycloak`) |

(T3 observability stack and T4 Kubernetes are out of scope for now.)

## Running

Live verification is **opt-in**: set `MCP_HANGAR_LIVE_VERIFY=1`, otherwise every
`live`-marked test skips (so a normal `pytest tests/` never starts servers).
`-o addopts=""` drops the repo's default `--cov` flags (coverage of a subprocess
is meaningless here).

```bash
# T0 only (no Docker needed):
MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0" -o addopts=""

# everything available (T1/T2 skip if their prerequisites are absent):
MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m live -o addopts=""
```

The `live-verify` GitHub workflow (manual + nightly) sets the env var for you.

## How to add a verification

Each test encodes one **falsifiable claim** from
`docs/internal/LIVE_VERIFICATION.md`: drive the behaviour through the real
surface (a tool call / REST endpoint / CLI), then assert on an observable
(result, metric, audit event, state transition, or HTTP status). Build T0 tests
on the `live_http_hangar` fixture in `conftest.py`; add compose/Keycloak
fixtures for T1/T2 there as those tiers are filled in.
