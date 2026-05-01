# ADR-004: Preemptive Implementation of SEP-1766 (Digest Pinning) and SEP-1763 (Interceptor Framework)

**Status:** Accepted
**Date:** 2026-05-01
**Authors:** MCP Hangar Team

## Context

The MCP protocol ecosystem is developing two complementary proposals that directly align with MCP Hangar's mission:

- **SEP-1766** (Digest-Pinned Tool Versioning): Requires MCP servers to publish SHA256 digests for every tool, enabling drift detection and version pinning.
- **SEP-1763** (Interceptor Framework): Formalizes the concept of MCP interceptors that can validate, mutate, or observe MCP traffic at runtime.

Both SEPs are in open/proposal status as of 2026-05-01. Neither has been merged into the MCP specification yet.

MCP Hangar already implements the core functionality described by both proposals:
- hangar-agent intercepts all MCP traffic (SEP-1763 alignment)
- Policy engine evaluates calls against configurable rules (SEP-1763 enforcement)
- Audit logging captures all tool invocations (SEP-1763 observability)

What is missing: digest extraction from `tools/list`, digest-based allowlisting, and admin approval workflow for new digests (SEP-1766).

## Decision

We will implement SEP-1766 and SEP-1763 compliance **preemptively**, treating our implementation as the de facto standard. If the upstream spec changes before ratification, we will adapt.

### Rationale

1. **First-mover advantage.** Being the reference implementation of both SEPs positions MCP Hangar as the canonical MCP governance tool.
2. **Directional stability.** Both proposals solve real problems (tool mutation detection, runtime enforcement) that will not go away regardless of final spec shape.
3. **Low adaptation cost.** The core concepts (digest field, interceptor hooks) are stable. Only wire format or field naming might change, which is a mechanical refactor.
4. **Customer demand.** Enterprise buyers already ask for tool supply chain integrity. Waiting for spec ratification delays value delivery by 6-12 months.

### Design Choices

| Decision | Choice | Alternatives Considered |
|----------|--------|------------------------|
| Digest source of truth | Explicit admin approval | Auto-pin on first-seen (rejected: security risk) |
| Enforcement model | audit / warn / block per-org | Binary allow/deny (rejected: too rigid for adoption) |
| Servers without digest | Admin-configurable policy (allow-degraded / warn / block) | Always block (rejected: breaks backward compat) |
| Interceptor identity | hangar-agent declares itself as SEP-1763 interceptor | Silent proxy (rejected: loses MCP-native positioning) |

### Scope

| Component | What to implement |
|-----------|-------------------|
| hangar-agent | Extract `digest` from `tools/list` responses. Compare against cloud-provided allowlist. Enforce policy. Emit `DigestMismatchEvent`. |
| hangar-cloud | Store approved digests per org/workspace. CRUD API. Approval workflow (new -> pending -> approved/rejected). Audit trail. |
| Proto/API | New messages: `ToolDigest`, `DigestPolicy`, `DigestMismatchEvent`. Extend policy push with digest allowlists. |
| hangar-app | Digest management UI: approve/reject, drift alerts, tool change timeline. |
| operator | `allowedDigests` in MCPServer/MCPServerGroup CRD. DigestPolicy CRD. |
| mcp-hangar (Python) | Local digest computation helper. Standalone validation in non-cloud mode. |

## Consequences

### Positive

- MCP Hangar becomes the reference implementation for MCP supply chain integrity.
- Customers get tool drift detection without waiting for spec ratification.
- When SEPs are accepted, we are already compliant (or trivially adaptable).
- Competitive moat: other MCP tools must implement from scratch.

### Negative

- Risk of spec divergence: if final SEP changes digest algorithm or field structure, we refactor.
- Maintenance burden: must track upstream SEPs continuously (mitigated by mandatory protocol tracking in AGENTS.md).
- Customers may build on our implementation before spec stabilizes (mitigated by clear versioning and migration support).

### Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SEP-1766 changes digest field name/format | Medium | Low | Abstract behind internal `ToolDigest` value object; rename is mechanical |
| SEP-1766 rejected entirely | Low | Medium | Our implementation still provides value; rebrand as "Hangar Tool Integrity" |
| SEP-1763 defines interceptor API incompatible with agent | Low | Medium | Agent already uses adapter pattern; new adapter for spec-compliant interface |
| Competing implementations diverge from our choices | Medium | Low | We are the first implementation; community likely follows our lead |

## References

- [SEP-1766: Digest-Pinned Tool Versioning](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1766)
- [SEP-1763: Interceptor Framework](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1763)
- [SEP-1575: Tool Versioning](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1575)
- workspace/AGENTS.md -- MCP Protocol Compliance section (checklist + tracking)
