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
| **T0** | single process + a stub backend (`examples/provider_math`) — operational surface, `hangar_call`, lifecycle, tool-access policy, withdrawal/digest-pin, truncation/continuation | this checkout installed in the interpreter running pytest (`uv run`); the `mcp-hangar` on PATH is never used |
| **T1** | multi-backend / groups — group invocation, canary/failover, discovery, load-balancing | Docker + compose (≥2 backends) |
| **T2** | auth / IdP — JWT/OIDC, multi-issuer, RFC 8707 audience binding, `front_door` DENY, RBAC | Keycloak (`examples/auth-keycloak`) |
| **T3** | trace and audit export — `serve --http` exports to a real OTLP/gRPC receiver: a warm call's spans (with the upstream CLIENT span), a tool-access-denied call's span and no CLIENT span, a `tool_invocation` audit record under scope `mcp_hangar.audit`, the spans of a call made right before SIGTERM, a failing tool's error text reaching the caller but no exported span (no status message, event or attribute), and the tracing scenarios of #1304 for an API-key tenant: a cold start shared by ten callers, retries, approval outcomes, gate decisions and a collector that goes away, and a relayed task's follow-ups on both topologies: each `tasks/get`, `tasks/update` and `tasks/cancel` is a `task_relay.<op>` span under its own request's SERVER span with exactly one link to the creating `batch.call.<tool>`, another tenant's poll is `not_found` with no link, and no task id reaches a span (`_task_server.py` mints the tasks) | `grpcio` + the `opentelemetry` extra; the receiver runs in the test process (no Collector, no Docker) |
| **T4** | two gateways over one PostgreSQL (ADR-030, #1949) — a key list or endpoint changed on one replica is refused on both, stale writes and creates from a replica that has not caught up never revert the row, the peer-key name record seen by a second and a freshly started replica, one `key_ref` registered on both at once goes to one server, a forget racing a re-create, and the published 2.31.0 next to this checkout | `HANGAR_TEST_POSTGRES_DSN` (a server the test may create databases on), the `postgres` extra, a non-loopback host address, and `uv` for the published release |

(Kubernetes is out of scope for now. T3 does not cover a Collector, inbound or
outbound W3C trace context, or a trace link from an audit record to its call;
the audit record's trace ID is only reported.)

## Running

Live verification is **opt-in**: set `MCP_HANGAR_LIVE_VERIFY=1`, otherwise every
`live`-marked test skips (so a normal `pytest tests/` never starts servers).

```bash
# T0 only (no Docker needed):
MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t0"

# T3 only (skips if grpcio or the OTLP protos are missing):
MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t3"

# T4 only (skips without the DSN; see the module docstring for a local server):
HANGAR_TEST_POSTGRES_DSN='host=127.0.0.1 port=55432 user=hangar password=hangar dbname=hangar' \
    MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m "live and t4"

# everything available (T1-T4 skip if their prerequisites are absent):
MCP_HANGAR_LIVE_VERIFY=1 uv run pytest tests/live -m live
```

The `live-verify` GitHub workflow (manual + nightly) sets the env var for you.

## How to add a verification

Each test encodes one **falsifiable claim** from
`docs/internal/LIVE_VERIFICATION.md`: drive the behaviour through the real
surface (a tool call / REST endpoint / CLI), then assert on an observable
(result, metric, audit event, state transition, or HTTP status). Build T0 tests
on the `live_http_hangar` fixture in `conftest.py`; add compose/Keycloak
fixtures for T1/T2 there as those tiers are filled in.
