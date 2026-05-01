# ADR-005: SEP-1763 Interceptor Framework Compliance

**Status:** Accepted
**Date:** 2026-05-01
**Authors:** MCP Hangar Team

## Context

SEP-1763 (Interceptors for Model Context Protocol) proposes a standardized interceptor framework for MCP. The original issue (#1763, Nov 2025) was closed April 22, 2026 and superseded by PR #2624 with a refined specification. A formal working group was chartered (Apr 21, 2026) with biweekly meetings, and an experimental multi-language SDK lives at `modelcontextprotocol/experimental-ext-interceptors`.

MCP Hangar's hangar-agent is already a production interceptor sidecar:
- L7 proxy intercepting all MCP traffic
- Policy engine with audit/warn/block modes
- Trust-boundary-aware execution (different rules for inbound vs outbound)
- Event buffer with WAL for async observability shipping
- Graceful degradation when control plane is unreachable

The spec has evolved significantly from the original 3-type model (validation/mutation/observability) to a 2-type model (Validator + Mutator) with audit mode on both types, hook-based event model, `failOpen` policy, and formalized trust-boundary execution ordering.

## Decision

We will align hangar-agent with the SEP-1763 spec (PR #2624) as it evolves, positioning the agent as a reference sidecar runtime implementation. We implement incrementally: first align what we already have with spec terminology, then add missing capabilities.

### What We Already Have (Alignment Map)

| SEP-1763 Concept | hangar-agent Today | Gap |
|------------------|-------------------|-----|
| Validator (enforce mode) | Policy engine rules, block/allow | Terminology only |
| Validator (audit mode) | Audit mode — logs decisions, never blocks | Terminology only |
| Trust-boundary execution order | Different rules for inbound (`tools/list` responses) vs outbound (`tools/call` requests) | Verify ordering matches spec (Mutate->Validate->Send / Validate->Mutate->Process) |
| Observability / audit pipeline | Event buffer + WAL + gRPC shipping | Already exceeds spec requirements |
| `failOpen` | Agent-level failopen/failclose config | Need per-interceptor granularity |
| Lifecycle events: `tools/list`, `tools/call` | Intercepted today | Need `resources/*`, `prompts/*`, wildcards |

### What We Need to Add

| Capability | Description | Priority |
|-----------|-------------|----------|
| `interceptors/list` JSON-RPC method | Expose agent as discoverable interceptor per spec | P1 |
| `interceptor/invoke` JSON-RPC method | Allow explicit invocation (for non-transparent mode) | P2 |
| Hook-based event model | Migrate from flat event types to hook objects (event + phase wrapping) | P1 |
| Mutator type | Input/output transformation (PII redaction, schema enforcement) with sequential priority-based ordering | P1 |
| Shadow mutations (audit mode on mutators) | Compute transformation without applying; log "what would have changed" | P2 |
| Per-interceptor `failOpen` | Granular fail-open/fail-closed per interceptor rule, not just agent-level | P2 |
| Wildcard event subscription | Support `*`, `tools/*`, `*/request`, `*/response` patterns in policy config | P1 |
| Extended lifecycle events | `resources/*`, `prompts/*`, `sampling/*`, `elicitation/*`, `roots/*` interception | P2 |
| Priority-based mutator ordering | `priorityHint` field (-2B to +2B) for sequential mutator execution | P1 |

### Design Choices

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Implementation mode | Transparent sidecar (primary) + explicit `interceptor/invoke` (secondary) | Sidecar is our deployment model; explicit mode for SDK integrations |
| Mutator scope (initial) | PII redaction, argument schema enforcement, response truncation | Highest customer demand; builds on existing truncation infrastructure |
| Hook model migration | Internal abstraction layer; expose spec-compliant hooks externally, keep internal event types for backward compat | Non-breaking migration path |
| Working group engagement | Monitor and contribute when spec touches sidecar runtime | Influence spec toward our architecture without over-committing |

## Consequences

### Positive

- hangar-agent becomes a spec-compliant MCP interceptor — "MCP-native" positioning
- Working group is building exactly what we already have (sidecar runtime) — we can contribute and shape the standard
- Mutator type adds new product capabilities (PII redaction, schema enforcement) beyond pure policy enforcement
- Shadow mutations enable "what-if" analysis for policy planning — strong enterprise feature

### Negative

- Spec is still in flux (PR #2624 is OPEN) — we may need to adapt as it evolves
- Hook model migration requires touching event pipeline internals
- Mutator ordering adds complexity to the policy engine execution path

### Upstream Tracking

The SEP-1763 successor (PR #2624) and working group outputs MUST be checked before any milestone touching:
- MCP traffic interception
- Policy engine execution model
- Event types / lifecycle hooks
- Agent discoverability or identity

See workspace/AGENTS.md "Upstream Protocol Tracking" section for the mandatory checklist.

## References

- [SEP-1763 (closed)](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1763)
- [PR #2624 (successor, OPEN)](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2624)
- [Experimental implementation](https://github.com/modelcontextprotocol/experimental-ext-interceptors)
- [Working group charter](https://modelcontextprotocol.io/community/interceptors/charter)
- ADR-004: SEP-1766 Digest Pinning (companion decision)
