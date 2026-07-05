# Live verification matrix

Purpose: confirm that every feature declared **production-ready** actually
behaves as claimed when hangar is driven the way a real client/operator drives
it — over the MCP protocol, the REST/HTTP surface, and the CLI — not via
internal Python APIs. The value is catching any feature that declares more than
it delivers.

This is the map. The harness lives in [`tests/live/`](../../tests/live/README.md)
(opt-in, runs via the `live-verify` workflow). Fill rows in tier order; flip the
status as each claim gets a live test.

## How the existing suite leaves a gap

`tests/unit/` and `tests/integration/` are extensive, but a coverage audit found
that **no test starts a hangar MCP server and drives the real tool surface**.
The integration suite reaches features through internal objects (`McpServer`,
`McpServerGroup`, `ToolAccessResolver`, `BatchExecutor._execute_call`,
`CommandBus`, `DigestValidator`) — never through `hangar_call` / `hangar_*` over
MCP, REST, or the CLI. So most "stable" claims are proven *in-process*, not
*as-shipped*. That is exactly what live verification closes.

## Tiers

| Tier | Scope | Prerequisite |
|------|-------|--------------|
| T0 | single process + stub backend (`examples/provider_math`) | `mcp-hangar` on PATH |
| T1 | multi-backend / groups | Docker + compose |
| T2 | auth / IdP | Keycloak (`examples/auth-keycloak`) |

Status legend: ✅ live test exists · 🟡 covered only internally/mocked (NOT proven
live) · 🔴 no coverage at all · ⬜ live test not yet written.

## Matrix

### T0 — single process + stub backend

| Claim | Driven via | Observable proof | Existing coverage | Status |
|-------|-----------|------------------|-------------------|--------|
| `serve --http` starts and serves its operational surface | CLI + HTTP | `/health/live` 200, `/metrics` has `mcp_hangar_*` | `tests/live/test_t0_smoke.py` | ✅ |
| `hangar_call` runs a batch in parallel and returns each result | MCP `hangar_call` | result payloads, wall-clock < sum | internal only (`test_trace_propagation_e2e`, `test_batch_invoke`) | 🟡 |
| Management tools return correct shapes (`hangar_list`/`details`/`health`/`start`/`stop`/`load`/`warm`/`status`/`tools`/`metrics`/`reload_config`/`quarantine`/`sources`) | MCP tools | tool result JSON | unit only | 🟡 |
| Lifecycle COLD→READY→DEGRADED→DEAD + single-flight cold start | MCP `hangar_load`/`hangar_call` | state via `hangar_status` | internal (`test_e2e_mcp_flow`) | 🟡 |
| Tool-access policy (glob allow/deny, 3-level merge) blocks a denied tool on a real call **and** hides it from `hangar_tools` | MCP `hangar_call`/`hangar_tools` | rejection + filtered listing | internal resolver (`test_tool_filtering`) | 🟡 |
| Per-tenant withdrawal rejects a withdrawn tool on the call path; config-reload restores it | MCP `hangar_call` + reload | `CallResult(success=False)` then success | unit only | 🔴 |
| Digest pinning blocks a drifted tool and emits `DigestMismatchEvent` (#276/#280) | MCP `hangar_call` | rejection + event | validator internal only | 🔴 |
| Flat per-tenant re-export surfaces tools under flat names | MCP `tools/list` | re-exported names | unit only | 🔴 |
| Truncation + continuation (`hangar_fetch_continuation`/`delete_continuation`) | MCP tools | truncated payload then paged fetch | unit-ish only | 🔴 |
| Approval gate via `hangar_approve` / approval REST | MCP tool + REST | pending→resolve→granted | REST fakes (`test_approval_api_e2e`) | 🟡 |
| Hot reload via SIGHUP / `hangar_reload_config` takes effect | signal + MCP tool | reloaded state | file-watch real, effect mocked | 🟡 |
| OTEL trace context (W3C) propagates Agent→hangar→backend | MCP `hangar_call` + collector | correlated spans | mocked ctx | 🟡 |
| Audit log / CEF emitted on a real invocation | MCP `hangar_call` | CEF line in sink | exporter unit-ish | 🟡 |

### T1 — multi-backend / groups

| Claim | Driven via | Observable proof | Existing coverage | Status |
|-------|-----------|------------------|-------------------|--------|
| `hangar_call` to a group routes to a selected member (#282) | MCP `hangar_call` | call reaches a member backend | unit only (`test_group_invoke_routing`) | 🔴 |
| Canary: a pinned tenant deterministically hits its member; a split routes ~split_pct (#283) | MCP `hangar_call` per tenant | which member served | unit only (`test_canary_routing`) | 🔴 |
| Failover: a failed member leaves rotation; `report_failure` feeds the group breaker | MCP `hangar_call` under fault | next call avoids the dead member | internal `select_member` only | 🟡 |
| Load-balancing strategies distribute across members | MCP `hangar_call` ×N | member distribution | internal only | 🟡 |
| Discovery (filesystem/container) surfaces backends via `hangar_discover`/`discovered`/`sources` | MCP tools | discovered set | internal + **non-gating** script | 🟡 |

### T2 — auth / IdP (Keycloak)

| Claim | Driven via | Observable proof | Existing coverage | Status |
|-------|-----------|------------------|-------------------|--------|
| `front_door` mode DENIES an unauthenticated request (fail-closed) | HTTP/MCP no token | 401/deny, not silent allow | `tests/live/test_t2_auth.py::test_unauthenticated_front_door_call_is_denied` | ✅ |
| A signed OIDC token authenticates; `tenant_id` extracted from the claim | HTTP + Keycloak token | authenticated principal (tenant proven fail-closed via `require_tenant`) | `tests/live/test_t2_auth.py::test_valid_oidc_token_authenticates_and_carries_tenant` | ✅ |
| Multi-issuer: tokens from ≥2 trusted issuers both validate; untrusted issuer rejected (#273) | HTTP + 2 issuers | accept/reject | `tests/live/test_t2_auth.py::test_realm_b_token_is_accepted`, `::test_token_from_untrusted_issuer_is_rejected` | ✅ |
| RFC 8707 audience binding: token without matching `aud` rejected (#274) | HTTP token | rejection | `tests/live/test_t2_auth.py::test_aud_mismatch_is_rejected` | ✅ |
| PRM advertises all trusted issuers; 401 carries `WWW-Authenticate: resource_metadata` (RFC 9728) | `GET /.well-known/oauth-protected-resource` | `authorization_servers` list, header | `tests/live/test_t2_auth.py::test_prm_advertises_trusted_issuers` (+ `WWW-Authenticate` asserted in deny/untrusted tests) | ✅ |
| API-key rotation + grace; old key honored then rejected | REST/MCP with keys | accept→grace→reject | unit only | 🔴 |
| RBAC: a role lacking a permission is denied on a real call | HTTP `POST /api/mcp_servers/` (write) / MCP `hangar_call` | 403 for `viewer`, 2xx for `developer` | `tests/live/test_t2_auth.py::test_rbac_denies_unprivileged_and_allows_privileged` (live probe; **currently SKIPS** — see note) | 🔴 |

> **RBAC live finding (fail-OPEN).** The RBAC-denial row stays 🔴 because the claim
> could NOT be proven live: on the shipped `serve` HTTP surface a read-only `viewer`
> OIDC token performed a write-privileged `POST /api/mcp_servers/` and received `201`
> (identical to `developer`), i.e. authorization is not enforced. RBAC is
> role-store-driven (roles seeded from config `role_assignments`, keyed by
> `group:<name>` and joined on the token's `groups` claim; NOT taken from the token's
> `roles` claim), and the role store is populated correctly — but the per-endpoint
> guard `_check_permission` (`server/api/mcp_servers.py`) reads `auth_components` from
> the global `ApplicationContext` via `get_context()`, which `bootstrap` installs with
> `init_context(runtime)` (`server/bootstrap/__init__.py`) and never sets
> `ctx.auth_components` on, so `authz_middleware` is `None` and the check returns early.
> The MCP `hangar_call` path likewise never consults `tool:invoke`. Authentication is
> wired; authorization is not. The live test asserts the intended invariant and
> auto-passes (flipping this row to ✅) once the wiring is fixed; until then it skips
> with this finding rather than faking a pass.

## Priority gaps (proven nowhere live)

Ranked by risk × recency — these are unit/internal only and should get live tests first:

1. **The entire real MCP tool surface** — nothing drives `hangar_call`/`hangar_*` over MCP. Standing up T0 against the stub backend unlocks most of the T0 rows at once.
2. **Auth / front_door (T2)** — multi-issuer (#273), audience binding (#274), `front_door` fail-closed DENY, OIDC tenant extraction, and PRM are now proven live against a real Keycloak (`tests/live/test_t2_auth.py`). Still unit-only: API-key rotation/grace. RBAC permission denial now has a live probe (`test_rbac_denies_unprivileged_and_allows_privileged`) that surfaced a fail-OPEN gap — authorization is not enforced on the `serve` surface (see the RBAC note above); it stays 🔴 and skips until the wiring is fixed.
3. **Group invocation + canary (T1, #282/#283)** — routing is proven with mocks; never with a real call routed to a member.
4. **Per-tenant projection on the call path (T0)** — withdrawal enforcement, reload restore, digest pinning (#276/#280): the executor path is unverified live.
5. **Continuation** (`hangar_fetch_continuation`/`delete_continuation`) — untested beyond the truncator unit.
6. **Persisted event sourcing** — only in-memory is proven; the Postgres container test uses ad-hoc SQL, not hangar's store.

## Keeping this current

Each new live test flips a row to ✅ and cites its file. When a feature is added
or a claim changes, add/adjust a row here in the same PR — this matrix is the
canonical "declared stable → proven live" ledger.
