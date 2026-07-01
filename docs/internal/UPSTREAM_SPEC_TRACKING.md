# Upstream Spec Tracking

Track the upstream MCP status the interceptor/governance extension depends on.

## Interceptors

**Original issue:** [modelcontextprotocol/spec#1763](https://github.com/modelcontextprotocol/spec/issues/1763)

**Current status:** PR #2624 is OPEN / experimental in the `modelcontextprotocol/experimental-ext-interceptors` repository (not merged into core spec).

**Spec shape:**
- Model: Validator + Mutator
- Methods: `interceptors/list` and `interceptor/invoke`
- Hook objects carry `events` and `phase` fields
- **Critical:** `failOpen` MUST default to false (fail-closed at trust boundaries)

**Our action:** Pin a specific spec revision. The wire format may still change upstream; we will track and update as needed.

## Digest Pinning

**Upstream ref:** SEP-1766 (UNSPONSORED draft)

**Status:** Do NOT build against this SEP. It is not advancing through the sponsorship process.

**Our approach:** Implement digest pinning as our own Validator extension, independent of upstream standardization.

## Extensions Framework

**Upstream ref:** SEP-2133 (core extensions spec)

**Adoption:** We use reverse-DNS IDs following the framework (adopted `io.mcp-hangar.*` in #346).

**Key requirement:** Extensions MUST be disabled by default and require explicit opt-in by the client.

## ADR-005 Revisit Note

ADR-005 framed interceptors as a core SEP. Current reality: the interceptor work lives on the extension/WG track, not core. The ADR assumptions about integration points may need revision as the extension model matures.

## Status Table

| Item | Ref | Status | Our Action |
|------|-----|--------|-----------|
| Interceptors spec | [#1763](https://github.com/modelcontextprotocol/spec/issues/1763) / PR #2624 | OPEN / experimental (not merged) | Pin a revision; track changes |
| Digest pinning | SEP-1766 | UNSPONSORED draft | Implement as own Validator |
| Extensions framework | SEP-2133 | Core spec | Adopt reverse-DNS IDs; enforce default-off |
| ADR-005 assumption review | ADR-005 | Needs update | Reflect extension-track reality |
