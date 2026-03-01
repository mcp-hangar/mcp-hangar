# MCP Hangar

## What This Is

MCP Hangar is a production-grade infrastructure platform for Model Context Protocol (MCP) providers. It manages provider lifecycle (subprocess, Docker, remote HTTP), load balancing across provider groups, auto-discovery (Kubernetes, Docker, filesystem, entrypoints), and exposes tools via MCP protocol. Includes a Kubernetes operator with MCPProvider, MCPProviderGroup, and MCPDiscoverySource custom resources, plus comprehensive documentation. Designed for environments with thousands of engineers and zero tolerance for mystery failures.

## Core Value

Reliable, observable MCP provider management with production-grade lifecycle control -- providers start, run, degrade, and recover predictably with full audit trail.

## Current State

v0.10 Documentation & Kubernetes Maturity shipped 2026-03-01. Comprehensive reference and guide documentation for configuration, tools, provider groups, and facade API. Kubernetes operator feature-complete with MCPProviderGroup and MCPDiscoverySource controllers, envtest integration tests. Both Helm charts synchronized to v0.10.0 with NOTES.txt and test templates.

## Requirements

### Validated

- JWT authentication with JWKS/OIDC discovery (token validation)
- API key authentication with SHA-256 hashing
- RBAC authorization with role-based tool access
- OPA policy-based authorization
- Auth middleware pipeline (authenticate -> authorize -> execute)
- Auth stores: SQLite, PostgreSQL, event-sourced
- Per-IP rate limiting (AuthRateLimiter) with lockout logic
- Domain events for auth success/failure (audit trail)
- Tool access filtering with allow/deny lists (fnmatch patterns)
- Constant-time API key comparison (hmac.compare_digest) -- v0.9
- Exponential backoff rate limiting with domain events -- v0.9
- JWT max token lifetime enforcement (configurable) -- v0.9
- API key rotation with grace period (rotate_key, KeyRotated event) -- v0.9
- Configuration Reference page (full YAML schema, env vars, validation rules) -- v0.10
- MCP Tools Reference page (all 22 tools with parameters, returns, side effects) -- v0.10
- Provider Groups Guide (5 strategies, health policies, circuit breaker, tool filtering) -- v0.10
- Facade API Guide (Hangar/SyncHangar API, HangarConfig builder, framework integration) -- v0.10
- MCPProviderGroup controller (label selection, status aggregation, health policy evaluation) -- v0.10
- MCPDiscoverySource controller (4 modes, additive/authoritative, owner references) -- v0.10
- envtest integration tests for both Kubernetes controllers -- v0.10
- Helm charts version-synchronized to 0.10.0 with NOTES.txt and test templates -- v0.10

### Active

- Fix broken documentation links, stale version references, old org name across 8 files (DEFER-01)
- CRD API Reference auto-generated from Go types (DEFER-02)
- Troubleshooting and operational runbooks for production deployments (DEFER-03)
- Helm values.schema.json for IDE validation and helm lint enforcement (DEFER-04)
- MCPProviderGroup automatic rebalancing on member changes (DEFER-05)

### Out of Scope

- API key IP binding (allowed_ips per key) -- deferred, adds complexity without immediate threat
- OIDC login flow (authorization code, redirects) -- MCP Hangar is a resource server, not an IdP
- mTLS between Hangar and providers -- recommended for production but separate concern
- Vault/HSM integration for key storage -- production deployment concern, not core auth hardening
- Distributed rate limiting (Redis-backed) -- single-node first, scale later
- Webhook admission controller -- kubebuilder validation markers sufficient, TLS cert management adds complexity
- CRD version conversion (v1alpha1 to v1beta1) -- API not stable yet, adds maintenance burden without demand
- Multi-cluster operator support -- massive complexity leap, no evidence of demand
- OLM packaging -- declining adoption outside OpenShift, Helm is dominant distribution mechanism
- Cross-namespace discovery -- security concern, violates least-privilege RBAC
- Auto-generated Python API docs from docstrings -- exposes internal domain code, facade guide covers public API
- Helm umbrella chart combining server + operator -- couples lifecycle of independent components

## Context

- Python 3.11+, DDD + CQRS + Event Sourcing architecture
- Go Kubernetes operator with controller-runtime (MCPProvider, MCPProviderGroup, MCPDiscoverySource CRDs)
- Auth layer in `packages/core/mcp_hangar/infrastructure/auth/`
- Domain security in `packages/core/mcp_hangar/domain/security/`
- Operator controllers in `packages/operator/internal/controller/`
- Documentation in `docs/` with MkDocs (Configuration, Tools, Provider Groups, Facade API references and guides)
- Two Helm charts: `packages/helm-charts/mcp-hangar/` (server) and `packages/helm-charts/mcp-hangar-operator/` (operator)
- Security audit documented in `docs/security/AUTH_SECURITY_AUDIT.md`
- Thread-safe design with lock hierarchy (Provider._lock -> StdioClient.pending_lock)
- All auth stores (InMemory, SQLite, Postgres, EventSourced) use constant-time key validation
- Rate limiter uses exponential backoff (2x factor, capped at 1 hour)

## Constraints

- **Architecture**: Domain layer has NO external dependencies. Layer dependencies flow inward only.
- **Thread safety**: All shared state changes must be thread-safe (existing RLock pattern).
- **Event sourcing**: State changes must emit domain events.
- **Backward compat**: Existing config must continue working. New config fields must have sensible defaults.
- **Python 3.11+**: Modern type hints (str | None, list[str]).
- **Go conventions**: controller-runtime patterns for Kubernetes operator.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Token validation only (no OIDC flow) | MCP Hangar is resource server | Good |
| SHA-256 for API key hashing | Standard, sufficient for comparison | Good |
| In-memory rate limiter (not distributed) | Single-node first, scale later | Good |
| hmac.compare_digest for all hash comparisons | C-level constant-time, resists timing attacks | Good -- v0.9 |
| Iterate all dict entries without early exit | Prevents timing side-channel on key position | Good -- v0.9 |
| Dummy hash comparison for SQL stores | Defense-in-depth beyond DB index timing | Good -- v0.9 |
| Exponential backoff factor^(count-1) | First lockout at base duration, progressive escalation | Good -- v0.9 |
| event_publisher optional callback pattern | Backward compatibility, safe publishing | Good -- v0.9 |
| max_token_lifetime=0 disables check | Escape hatch for environments needing no limit | Good -- v0.9 |
| 24h default grace period for key rotation | Balances security and operational convenience | Good -- v0.9 |
| Prevent cascading rotations | Avoids multiple grace periods on same key | Good -- v0.9 |
| MCPProviderGroup is read-only aggregator (no owner refs) | Groups observe, don't own providers | Good -- v0.10 |
| MCPDiscoverySource creates MCPProviders with owner refs | Discovery is the parent controller | Good -- v0.10 |
| Group Ready is threshold-based; Degraded+Ready coexist | Matches K8s patterns (partially available) | Good -- v0.10 |
| Authoritative sync deletes immediately (label-based tracking) | Clean semantics, scoped to successful scans | Good -- v0.10 |
| 7 tool categories matching source file organization | Reflects actual code structure over arbitrary grouping | Good -- v0.10 |
| Broken doc link fixes deferred to v0.11 | Not blocking v0.10 goals, separate concern | Pending |

---
*Last updated: 2026-03-01 after v0.10 milestone complete*
