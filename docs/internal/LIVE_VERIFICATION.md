# Live verification matrix

Purpose: confirm that every feature declared **production-ready** actually
behaves as claimed when hangar is driven the way a real client/operator drives
it — over the MCP protocol, the REST/HTTP surface, and the CLI — not via
internal Python APIs. The value is catching any feature that declares more than
it delivers.

This is the map. The harness lives in [`tests/live/`](../../tests/live/README.md)
(opt-in, runs via the `live-verify` workflow). Fill rows in tier order; flip the
status as each claim gets a live test.

**Last reconciled:** 2026-08-11 against core 2.5.2 and the current `tests/live/` tree.

## How the existing suite leaves a gap

> Historical framing — the audit that motivated this harness. `tests/live/` has since
> closed the T0 smoke, tool-access, withdrawal, digest, T1 group/canary, and the whole
> T2 auth column; the sections below carry the current status.

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
| `serve --http` starts and serves its operational surface | CLI + HTTP | `/health/live` 200, `/metrics` has `mcp_hangar_*` | `tests/live/test_t0_smoke.py::test_health_endpoint_responds`, `::test_metrics_endpoint_exposes_prometheus` | ✅ |
| Readiness is green with a configured but cold backend (no idle-gateway deadlock, #599) | CLI + HTTP | `/health/ready` green while every backend is COLD | `tests/live/test_t0_smoke.py::test_readiness_is_green_with_a_configured_but_cold_backend` | ✅ |
| With auth disabled the REST API answers instead of 401-ing (#600) | HTTP REST | 2xx, not 401 | `tests/live/test_t0_smoke.py::test_rest_api_is_reachable_when_auth_is_disabled` | ✅ |
| On stdio, stdout carries JSON-RPC and nothing else (#563) | CLI stdio | every stdout line parses as JSON-RPC | `tests/live/test_t0_smoke.py::test_stdio_stdout_carries_only_jsonrpc` | ✅ |
| `hangar_call` runs a batch in parallel and returns each result | MCP `hangar_call` | result payloads, wall-clock < sum | internal only (`test_trace_propagation_e2e`, `test_batch_invoke`) | 🟡 |
| Management tools return correct shapes (`hangar_list`/`details`/`health`/`start`/`stop`/`load`/`warm`/`status`/`tools`/`metrics`/`reload_config`/`quarantine`/`sources`) | MCP tools | tool result JSON | unit only | 🟡 |
| Lifecycle COLD→READY→DEGRADED→DEAD + single-flight cold start | MCP `hangar_load`/`hangar_call` | state via `hangar_status` | internal (`test_e2e_mcp_flow`) | 🟡 |
| Tool-access policy (glob allow/deny, 3-level merge) blocks a denied tool on a real call **and** hides it from `hangar_tools` | MCP `hangar_call`/`hangar_tools` | rejection + filtered listing | `tests/live/test_t0_tool_access.py` (per-tenant deny over `/mcp` with an `X-API-Key` tenant: denied tool `success=False` on `hangar_call` AND absent from `hangar_tools`, allowed tool callable + listed). Fixed a fail-OPEN on the listing half — see note below. | ✅ |
| Per-tenant withdrawal rejects a withdrawn tool on the call path; config-reload restores it | MCP `hangar_call` + reload | `CallResult(success=False)` then success | `tests/live/test_t0_withdrawal.py` (live: a tool withdrawn for tenant A is rejected on A's `hangar_call` with `ToolWithdrawnError`, while tenant B and A's other tools succeed -- proving the executor's per-tenant withdrawal check AND that the caller identity bridged over streamable-HTTP by #387 reaches it). Config-reload *restore* remains unit-only (`test_config_withdrawal::test_reload_clears_then_reapplies_withdrawals`). | ✅ |
| Digest pinning blocks a drifted tool and emits `DigestMismatchEvent` (#276/#280) | MCP `hangar_call` | rejection + event | `tests/live/test_t0_digest.py` (real API-key tenant + `provider_identity` stub: a tool pinned to a stale digest is rejected `CallResult(success=False, error_type="ToolDigestMismatchError")` and never dispatched; a matching pin is allowed). Enforcement confirmed **already fail-closed** over HTTP -- per-tenant identity (via `X-API-Key`) reaches the executor and the pin fires. `DigestMismatchEvent` emission on that same branch is unit-covered (`tests/unit/test_digest_pinning_executor.py`); it is internal, not observable over HTTP. | ✅ |
| Flat per-tenant re-export surfaces tools under flat names | MCP `tools/list` | re-exported names | unit only | 🔴 |
| Truncation + continuation (`hangar_fetch_continuation`/`delete_continuation`) | MCP tools | truncated payload then paged fetch | unit-ish only | 🔴 |
| Approval gate via `hangar_approve` / approval REST | MCP tool + REST | pending→resolve→granted | REST fakes (`test_approval_api_e2e`) | 🟡 |
| Hot reload via SIGHUP / `hangar_reload_config` takes effect | signal + MCP tool | reloaded state | file-watch real, effect mocked | 🟡 |
| OTEL trace context (W3C) propagates Agent→hangar→backend | MCP `hangar_call` + collector | correlated spans | mocked ctx | 🟡 |
| Audit log / CEF emitted on a real invocation | MCP `hangar_call` | CEF line in sink | exporter unit-ish | 🟡 |

> **Tool-access live finding (fail-OPEN on listing → FIXED).** Driving a per-tenant
> `deny_list` over the real `/mcp` surface surfaced a split-brain: the invoke path
> (`hangar_call` → `BatchExecutor`) correctly rejected the denied tool
> (`ToolAccessDeniedError`) because it keys the resolver on the caller
> `member_id=<tenant>`, but `hangar_tools` still LISTED it. Root cause: the listing
> helpers (`_get_tools_for_mcp_server`, `hangar_details`) called
> `resolver.filter_tools(...)` with NO `member_id`, so only the server-level policy
> applied and the per-tenant deny was ignored — a fail-OPEN on the visibility half
> of the claim (denied on call, yet advertised). The tenant was unavailable to the
> listing thread for the same reason as the group canary gap: over streamable-HTTP
> the ASGI auth layer's `identity_context_var` is not propagated into FastMCP's
> per-session tool task. Fixed by reading the caller tenant in the listing path
> (`server/tools/mcp_server.py::_caller_tenant_id`, bridging the request's
> authenticated principal exactly as `hangar_call` does) and passing `member_id` to
> the resolver, so listing and invocation now agree.

### T1 — multi-backend / groups

| Claim | Driven via | Observable proof | Existing coverage | Status |
|-------|-----------|------------------|-------------------|--------|
| `hangar_call` to a group routes to a selected member (#282) | MCP `hangar_call` | call reaches a member backend | `tests/live/test_t1_groups.py::test_group_invocation_routes_to_a_member` (2 subprocess `provider_identity` members; `whoami` echoes the server) | ✅ |
| Canary: a pinned tenant deterministically hits its member; a split routes ~split_pct (#283) | MCP `hangar_call` per tenant | which member served | `tests/live/test_t1_groups.py::test_canary_pins_a_tenant_to_a_version` (real per-tenant routing over `/mcp`: each pinned tenant is sticky to its pinned member, and every SHA-256-bucketed in-split tenant deterministically lands on the canary member). Proven live post-#389: the harness grants its callers `tool:invoke` via the built-in `service-account` role -- the seeded per-tenant keys share a group carrying the role, and the tenant-less warm/round-robin caller holds it too (the invoke path hard-denies an anonymous principal since #389, so a credential is required). The caller `tenant_id` reaching the executor over streamable-HTTP is the same #387 bridge proven by `tests/live/test_t0_withdrawal.py`; #283 bucketing also unit-covered by `test_canary_routing`. | ✅ |
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
| API-key rotation + grace; old key honored then rejected | REST/MCP with keys | accept→grace→reject | `tests/live/test_t2_apikey.py` (valid→200; rotated old key honored in grace + new key works; post-grace old key→401; revoked→401; all fail-closed) | ✅ |
| RBAC: a role lacking a permission is denied on a real call | HTTP `POST /api/mcp_servers/` (write) | 403 for `viewer`, 2xx for `developer` | `tests/live/test_t2_auth.py::test_rbac_denies_unprivileged_and_allows_privileged` (live probe; passes with the #386 wiring fix) | ✅ |
| The MCP `hangar_call` path enforces `tool:invoke` | MCP `hangar_call` over `/mcp` | `viewer` → `success=false`, `error_type="AuthorizationDenied"`, tool NOT executed; `developer` → never an authz denial | `tests/live/test_t2_auth.py::test_hangar_call_enforces_tool_invoke_viewer_denied_developer_allowed` (two real Keycloak tokens, one invocation; only the assigned role differs) | ✅ |
| The role gating a privileged op comes from the token's `groups` claim, not the username | HTTP `POST /api/mcp_servers/` ×3 users | `developer` allowed; `viewer` denied; `admin` **denied** because their group carries no assignment | `tests/live/test_t2_auth.py::test_group_claim_drives_the_mapped_role` | ✅ |
| Adding one `group:` → role assignment flips exactly that group's outcome | two gateways differing by one `role_assignments` entry | `admin` denied on one, allowed on the other; `viewer` denied on both | `tests/live/test_t2_auth.py::test_assigning_a_group_a_role_changes_that_group_only` | ✅ |

> **RBAC live finding (fail-OPEN → FIXED in #386).** This probe originally surfaced a
> fail-OPEN gap: on the shipped `serve` HTTP surface a read-only `viewer`
> OIDC token performed a write-privileged `POST /api/mcp_servers/` and received `201`
> (identical to `developer`), i.e. authorization was not enforced. Root cause below;
> fixed by wiring `auth_components` onto the context in #386, after which the probe
> passes live (`viewer` → 403 `AccessDeniedError`, `developer` → 201). RBAC is
> role-store-driven (roles seeded from config `role_assignments`, keyed by
> `group:<name>` and joined on the token's `groups` claim; NOT taken from the token's
> `roles` claim), and the role store is populated correctly — but the per-endpoint
> guard `_check_permission` (`server/api/mcp_servers.py`) read `auth_components` from
> the global `ApplicationContext` via `get_context()`, which `bootstrap` installed with
> `init_context(runtime)` (`server/bootstrap/__init__.py`) without ever setting
> `ctx.auth_components`, so `authz_middleware` was `None` and the check returned early.
> **Both halves are now closed.** The REST half was fixed in #386; the MCP
> `hangar_call` half — which this note originally recorded as "likewise never consults
> `tool:invoke`" — is enforced and proven live by
> `test_hangar_call_enforces_tool_invoke_viewer_denied_developer_allowed`. The broader
> fix landed with the route-driven authz chokepoint and the defined⇒enforced ratchet in
> core 2.2.0.

<!-- markdownlint-disable-next-line MD028 -->

> **`front_door` was decorative on the serve path (#596 → fixed in #612).** The T2 RBAC
> fixtures used to set `tool_access.mode: front_door`, which did nothing: the gate lived
> only in `MCPServerFactory`, which has **no production call site**, so the shipped
> `serve --http` bootstrap never applied it. Once #612 made the mode real, `hangar_call`
> was correctly no longer served in front_door (front_door replaces the `hangar_*`
> meta-API with flat backend names) and the fixture's premise broke — so `_RBAC_CONFIG`
> now deliberately stays on the default egress topology, and front_door semantics are
> asserted on the separate `hangar_oidc` fixture. Watch for this shape: anything wired
> only in `MCPServerFactory` is dead code on the shipped server.

## Priority gaps — status

Originally the ranked list of claims proven nowhere live. Most have since closed; the
entries are kept in their original order so the trail is readable, each marked with where
it now stands. Residual gaps: 4 (reload restore), 5, 6, and everything under
"Not yet in this matrix".

1. **The real MCP tool surface** — **largely closed.** `hangar_call` and `hangar_tools` are now driven over `/mcp` by the tool-access, withdrawal, digest, group and T2 authz tests. Still 🔴 on this surface: the flat per-tenant re-export (`tools/list`) and continuation (see 5).
2. **Auth / front_door (T2)** — **closed.** Multi-issuer (#273), audience binding (#274), `front_door` fail-closed DENY, OIDC tenant extraction, and PRM are proven live against a real Keycloak (`tests/live/test_t2_auth.py`). API-key rotation/grace is proven live on the real `serve --http` surface (`tests/live/test_t2_apikey.py`): a rotated key is honored during its grace window then rejected fail-closed once grace elapses, and a revoked key is rejected immediately. Authorization is now enforced and proven on **both** surfaces — REST (`test_rbac_denies_unprivileged_and_allows_privileged`, fixed in #386) and MCP `hangar_call` (`test_hangar_call_enforces_tool_invoke_viewer_denied_developer_allowed`) — plus the group-claim mapping (`test_group_claim_drives_the_mapped_role` and its single-assignment-delta companion).
3. **Group invocation + canary (T1, #282/#283)** — now proven live: `test_group_invocation_routes_to_a_member` (member selection) and `test_canary_pins_a_tenant_to_a_version` (per-tenant pins + split) both route real `hangar_call`s to real subprocess members. Remaining T1 gaps: failover, load-balancing strategy distribution, and discovery are still mock/internal only.
4. **Per-tenant projection on the call path (T0)** — mostly closed: withdrawal enforcement (`test_t0_withdrawal.py`) and digest pinning (#276/#280, `test_t0_digest.py`) are proven live on the executor path. **Config-reload *restore* remains unit-only** — that is the residual gap.
5. **Continuation** (`hangar_fetch_continuation`/`delete_continuation`) — untested beyond the truncator unit.
6. **Persisted event sourcing** — still unproven live, and the target moved: the ES spine was
   advertised long before it was wired (`events.db` held zero rows), and the remodel that
   actually wired it landed as ADR-018 / ADR-019 with the storage decision split across two
   backends. Nothing in `tests/live/` drives a persisted append + replay; the Postgres
   container test uses ad-hoc SQL rather than hangar's store. This is now the top gap.

## Not yet in this matrix

Shipped since this matrix was last reconciled, with **no row and no live test**. Each needs
a row before its claim can be called proven as-shipped:

- **Task relay with governance** (ADR-014 / ADR-015) — the governed lifecycle including
  interactive HITL consent was validated e2e on kind and by the `task-relay-smoke`
  workflow against `examples/task_upstream`, but never as a row here. Note `examples/**`
  is not covered by CI.
- **Approval resolution chokepoint + input-request namespace** (ADR-016 / ADR-017).
- **`MCPEgressPolicy` enforcement** (ADR-013) — operator-side, so it may belong in the
  operator repo's own matrix rather than here.
- **High availability** (ADR-020) — two-stack behaviour is exercised by the local HA lab,
  not by anything gating.
- **Trusted-hosts allowlist on the MCP endpoint** (#871) — merged, no row. Front-door
  tenant binding is in flight on `fix/front-door-tenant-identity` and should arrive with
  its own row.

## Keeping this current

Each new live test flips a row to ✅ and cites its file. When a feature is added
or a claim changes, add/adjust a row here in the same PR — this matrix is the
canonical "declared stable → proven live" ledger.
