# MCP Hangar

## What This Is

MCP Hangar is a production-grade infrastructure platform for Model Context Protocol (MCP) providers. It manages provider lifecycle (subprocess, Docker, remote HTTP), load balancing across provider groups, auto-discovery (Kubernetes, Docker, filesystem, entrypoints), and exposes tools via MCP protocol. Designed for environments with thousands of engineers and zero tolerance for mystery failures.

## Core Value

Reliable, observable MCP provider management with production-grade lifecycle control -- providers start, run, degrade, and recover predictably with full audit trail.

## Current State

v0.9 Security Hardening shipped 2026-02-15. Auth layer hardened with timing-safe key validation, exponential backoff rate limiting, JWT lifetime enforcement, and zero-downtime API key rotation.

## Current Milestone: v0.10 Documentation & Kubernetes Maturity

**Goal:** Close documentation gaps (broken links, missing reference pages, missing guides) and bring the Kubernetes operator to feature-complete status (MCPProviderGroup and MCPDiscoverySource controllers) with synchronized Helm charts.

**Target features:**

- Fix 29 broken documentation links, stale references, and old org name references
- Create Configuration Reference (full YAML schema, env vars, validation rules)
- Create MCP Tools Reference (all 22 tools with parameters, returns, side effects)
- Create Provider Groups Guide (strategies, health policies, circuit breaker)
- Create Facade API Guide (Hangar/SyncHangar, HangarConfig builder)
- Implement MCPProviderGroup Kubernetes controller (reconciler, tests)
- Implement MCPDiscoverySource Kubernetes controller (reconciler, tests)
- Synchronize Helm charts from 0.2.0 to 0.10.0 with test templates and NOTES.txt

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

### Active

- Fix broken documentation links, stale version references, old org name across 8 files
- Configuration Reference page documenting full YAML schema, all environment variables, validation rules
- MCP Tools Reference page documenting all 22 tools with parameters, return formats, side effects
- Provider Groups Guide covering load balancing strategies, health policies, circuit breaker, tool access filtering
- Facade API Guide covering Hangar/SyncHangar API, HangarConfig builder, framework integration
- MCPProviderGroup Kubernetes controller with label-based selection, status aggregation, health policy evaluation
- MCPDiscoverySource Kubernetes controller with 4 discovery modes, additive/authoritative, provider creation
- Helm charts version sync (0.2.0 to 0.10.0) with test templates and post-install instructions

### Out of Scope

- API key IP binding (allowed_ips per key) -- deferred, adds complexity without immediate threat
- OIDC login flow (authorization code, redirects) -- MCP Hangar is a resource server, not an IdP
- mTLS between Hangar and providers -- recommended for production but separate concern
- Vault/HSM integration for key storage -- production deployment concern, not core auth hardening
- Distributed rate limiting (Redis-backed) -- single-node first, scale later

## Context

- Python 3.11+, DDD + CQRS + Event Sourcing architecture
- Auth layer in `packages/core/mcp_hangar/infrastructure/auth/`
- Domain security in `packages/core/mcp_hangar/domain/security/`
- Security audit documented in `docs/security/AUTH_SECURITY_AUDIT.md`
- Auth subsystem: ~10,099 LOC across auth stores, rate limiter, JWT authenticator
- Thread-safe design with lock hierarchy (Provider._lock -> StdioClient.pending_lock)
- All auth stores (InMemory, SQLite, Postgres, EventSourced) use constant-time key validation
- Rate limiter uses exponential backoff (2x factor, capped at 1 hour)
- API key rotation supported across all 4 stores with configurable grace period

## Constraints

- **Architecture**: Domain layer has NO external dependencies. Auth hardening in infrastructure/domain only.
- **Thread safety**: All auth state changes must be thread-safe (existing RLock pattern).
- **Event sourcing**: Security state changes must emit domain events.
- **Backward compat**: Existing auth config must continue working. New config fields must have sensible defaults.
- **Python 3.11+**: Modern type hints (str | None, list[str]).

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

---
*Last updated: 2026-02-28 after v0.10 milestone started*
