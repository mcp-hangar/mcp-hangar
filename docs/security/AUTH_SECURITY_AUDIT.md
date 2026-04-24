# Authentication Security Audit

Last audit date: 2026-04-23 (v1.6 security hardening refresh)

## Scope

This audit covers the authentication, authorization, and request-enforcement paths in MCP Hangar after the v1.6 security hardening work:

- API key and JWT/OIDC authentication
- Role-based access control (RBAC) and `policy:write` authorization
- Tool access policies (TAP)
- HTTP and WebSocket auth enforcement
- Browser-oriented CSRF defense-in-depth on session suspension
- Enterprise boundary loading between core `src/` and optional `enterprise/`

## Current Security Posture

### Authentication

- API requests rely on `request.state.auth`; WebSocket / generic ASGI paths use `scope["auth"]`.
- HTTP and WebSocket auth enforcement now share one core implementation in `src/mcp_hangar/server/api/middleware.py`.
- WebSocket connections support `?token=` bearer mapping for clients that cannot set custom headers.
- Trusted proxy resolution is centralized through `TrustedProxyResolver`, preventing spoofed `X-Forwarded-For` from untrusted peers.

### Authorization

- `/api/agent/policy` no longer trusts a magic internal header.
- Policy push now requires an authenticated principal plus `policy:write` authorization.
- Failed policy pushes emit `PolicyPushRejected` audit events.
- The `agent` role includes the explicit `policy:write` permission required by hangar-agent.

### Browser CSRF Defense

- CSRF enforcement is intentionally scoped to browser-style session suspension requests.
- `POST /sessions/{session_id}/suspend` requires `X-Requested-With` only when the request looks browser-originated (`Origin`, `Referer`, or `Cookie` present).
- API key clients, bearer-token clients, and non-browser API callers bypass the CSRF check.
- This keeps REST API automation compatible while still defending against browser-triggered session suspension.

### WebSocket Security

- Authentication failures close the socket with code `1008` before the connection is used.
- `Origin` validation happens before `websocket.accept()` to mitigate cross-site WebSocket hijacking.
- Per-connection backpressure is enforced with bounded queues.

### Enterprise Boundary

- Core bootstrap/router code no longer scatters direct `enterprise.*` imports across server modules.
- Optional enterprise integrations are resolved via `src/mcp_hangar/server/bootstrap/enterprise.py`.
- The boundary exposes provider hooks for:
  - license validation
  - auth CQRS registration
  - API route extension
  - enterprise-backed event store creation
  - observability adapter creation
  - legacy bootstrap compatibility exports
- Entry points are supported when available; the monorepo layout uses a controlled fallback loader so development remains functional without breaking the core boundary.

## Findings Status

| Finding | Status | Notes |
|--------|--------|-------|
| K-1 Agent policy auth bypass | Fixed | `/api/agent/policy` requires authenticated principal + `policy:write`; rejection events emitted |
| K-2 WebSocket auth / CSWSH gaps | Fixed | Shared auth enforcement, pre-accept Origin validation, bounded queue backpressure |
| K-3 Unsafe unauthenticated HTTP exposure | Fixed | Non-loopback HTTP bind blocked without auth unless explicitly overridden |
| K-4 CORS / host / CSRF hardening | Fixed | Explicit CORS config, TrustedHostMiddleware, browser-scoped CSRF defense |
| W-1/W-2 Header identity spoofing via proxies | Fixed | Trusted proxy resolution centralized and required for forwarded identity trust |
| W-3 SSRF on remote endpoints | Fixed | SSRF validation blocks private/link-local targets |
| W-4 Unbounded suspended-session cache | Fixed | TTL-bounded cache with max size |
| W-5 JWT algorithm confusion | Fixed | Mixed symmetric/asymmetric algorithm families rejected |
| A-5 Core importing enterprise directly | Fixed | Server bootstrap/router path moved behind single core enterprise boundary |
| A-7 Divergent HTTP/WS auth middleware | Fixed | Core shared auth middleware path now handles both |

## Recommendations

| Item | Status | Notes |
|------|--------|-------|
| API key hash-only storage | Pass | Raw keys are not persisted |
| JWT algorithm-family validation | Pass | Mixed HS*/RS*/ES*/PS* families rejected |
| Trusted proxy validation | Pass | Only configured proxies may influence forwarded source identity |
| WebSocket origin validation | Pass | Performed before accept |
| Shared auth logic across protocols | Pass | One core implementation reduces drift |
| Core/enterprise import boundary | Pass | Centralized in `server/bootstrap/enterprise.py` |
| Repo-wide Ruff cleanliness | Follow-up | `uv run ruff check src/ tests/` still reports historical unrelated test lint debt |
| Manual exploit verification | Pass | Replay confirmed K-1 returns 401, K-2 closes with 1008, K-3 aborts non-loopback startup with `SystemExit(1)`, and K-4 rejects hostile hosts with 400 under strict CORS |
| TLS / mTLS at deployment edge | Manual | Must be enforced by deployment topology / reverse proxy |

## Verification Evidence

- `uv run pytest tests/ -x -q` -- pass
- `uv run mypy src/` -- pass
- Manual exploit replay of K-1..K-4 -- pass (`/agent/policy/` spoof returns 401, unauthenticated WebSocket closes 1008, non-loopback no-auth HTTP exits 1, hostile Host rejected with 400)
- Focused security and boundary suites cover:
  - `tests/security/test_critical.py`
  - `tests/security/test_identity_network.py`
  - `tests/unit/test_bootstrap_enterprise_boundary.py`
  - `tests/unit/test_bootstrap_enterprise_loading.py`
  - `tests/unit/test_api_auth_enforcement.py`
  - `tests/unit/test_agent_policy.py`
  - `tests/unit/test_ws_auth.py`

## Open Items

- Repo-wide Ruff debt in historical tests remains outside the scope of this hardening pass.
- If enterprise packaging is split into a separately installed distribution later, the fallback loader can be retired in favor of entry-point-only discovery.
