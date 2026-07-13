# Upstream Spec Tracking

Track the upstream MCP status the interceptor/governance extension depends on.

## Interceptors

**Original issue:** [modelcontextprotocol/spec#1763](https://github.com/modelcontextprotocol/spec/issues/1763)

**Current status:** SEP-1763 was closed as completed on 2026-04-22. Its successor, PR [#2624](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2624), remains OPEN on the experimental working-group track and is not merged into the core spec.

**Spec shape:**

- Model: Validator + Mutator
- Methods: `interceptors/list` and `interceptor/invoke`
- Hook objects carry `events` and `phase` fields
- **Critical:** `failOpen` MUST default to false (fail-closed at trust boundaries)

**Schema drift:** Upstream moved the schema pin from `5bd7ab4` to `99bc7c9` and aligned the capability key with SEP-2133. Hangar reconciled that change in [#405](https://github.com/mcp-hangar/mcp-hangar/pull/405); the SEP prose pin remains `8029c78` while PR #2624 is open.

**Our action:** Pin a specific spec revision. The wire format may still change upstream; we will track and update as needed.

## Digest Pinning

**Upstream ref:** SEP-1766

**Status:** Closed as completed on 2026-06-24. The closed proposal was not merged into the upstream specification, so it is not a protocol dependency for Hangar.

**Our approach:** Keep `TaskDigestGuard` as our own Validator extension, independent of upstream standardization.

## Extensions Framework

**Upstream ref:** SEP-2133 (core extensions spec, merged by the 2026-07-08 upstream audit)

**Adoption:** We use reverse-DNS IDs following the framework (adopted `io.mcp-hangar.*` in #346).

**Key requirement:** Extensions MUST be disabled by default and require explicit opt-in by the client.

## Upstream Release Position

The 2026-05-21 upstream blog announced 2026-07-28 as the official RC. At the 2026-07-08 audit, no dated schema folder had been cut for that release: `2025-11-25` remained the newest dated folder and RC content lived in `draft/`.

The following tracked SEPs had merged into upstream spec `main` by that audit: SEP-414, SEP-1865, SEP-2133, SEP-2243, SEP-2468, SEP-2549, SEP-2567, SEP-2575, SEP-2577, and SEP-2663 (Tasks).

## ADR-005 Revisit Note

ADR-005 framed interceptors as a core SEP. Current reality: the interceptor work lives on the extension/WG track, not core. The ADR assumptions about integration points may need revision as the extension model matures.

## Status Table

| Item | Ref | Status | Our Action |
|------|-----|--------|-----------|
| Interceptors spec | [#1763](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1763) / [PR #2624](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/2624) | SEP-1763 closed completed 2026-04-22; successor PR remains OPEN / experimental | Pin `8029c78`; track changes; schema drift reconciled in [#405](https://github.com/mcp-hangar/mcp-hangar/pull/405) |
| Digest pinning | [SEP-1766](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1766) | Closed completed 2026-06-24; not merged into upstream spec | Keep `TaskDigestGuard` as own Validator |
| Extensions framework | SEP-2133 | Merged into upstream spec `main` by 2026-07-08 audit | Adopt reverse-DNS IDs; enforce default-off |
| Upstream SEP set | SEP-414, 1865, 2133, 2243, 2468, 2549, 2567, 2575, 2577, 2663 | Merged into upstream spec `main` by 2026-07-08 audit | Track RC content in `draft/` until a dated schema folder is cut |
| ADR-005 assumption review | ADR-005 | Needs update | Reflect extension-track reality |
