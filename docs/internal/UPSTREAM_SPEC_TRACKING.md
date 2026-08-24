# Upstream Spec Tracking

Track the upstream MCP status the interceptor/governance extension depends on.

**Last reconciled:** 2026-08-11 against core 2.5.2.

## Interceptors

**Upstream repo:** [`modelcontextprotocol/experimental-ext-interceptors`](https://github.com/modelcontextprotocol/experimental-ext-interceptors) — the work moved out of the main spec repo onto its own experimental extension repo. The original issue was [spec#1763](https://github.com/modelcontextprotocol/spec/issues/1763), closed as completed on 2026-04-22.

**Governing decision:** [ADR-012](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-012-interceptor-sep-pin-tracking-policy.md) — vendor + freeze at a known-good SHA, bump on a deliberate cadence, keep the surface experimental and off-by-default, detect drift on a schedule.

**Current pin:** `2f66b9b`. The canonical, machine-readable pin lives in `.github/workflows/interceptor-pin-drift.yml` (`PINNED_SHA`) — that workflow runs on `main` weekly and opens an informational issue when upstream `HEAD` moves ahead. Pin history: `5bd7ab4` → `99bc7c9` (#405, capability key aligned to the SEP-2133 extensions format) → `7cf90c9` (#655, 2026-07-29; review found the server-declared `interceptors/list` shape untouched, so no schema change) → `8704137` (#840, 2026-08-18; review found the three intervening commits are C#-SDK-only, Go-SDK-only, and Go-deps/CI-only — `docs/sep.md` untouched, so no schema change)) → `2f66b9b` (#1052, 2026-08-24; review found `docs/sep.md` changed only a broken issues URL and one doc comment, `Enforce mode` → `Active mode`, following the Go SDK's rename — the `Interceptor` interface and the `mode` enum are unchanged, so no schema change).

**Spec shape:**

- Model: Validator + Mutator
- Methods: `interceptors/list` and `interceptor/invoke`
- Hook objects carry `events` and `phase` fields
- **Critical:** `failOpen` MUST default to false (fail-closed at trust boundaries)

**Our action:** Bumping `PINNED_SHA` and re-deriving the vendored schema (`tests/unit/test_interceptors_list_schema.py`) happen in the same change — never one without the other. Never fetch upstream at runtime. Revisit toward a hard freeze once the SEP reaches an accepted/stable state.

## Digest Pinning

**Upstream ref:** SEP-1766

**Status:** Closed as completed on 2026-06-24. The closed proposal was not merged into the upstream specification, so it is not a protocol dependency for Hangar.

**Our approach:** Keep `TaskDigestGuard` as our own Validator extension, independent of upstream standardization.

## Extensions Framework

**Upstream ref:** SEP-2133 (core extensions spec, merged by the 2026-07-08 upstream audit)

**Adoption:** We use reverse-DNS IDs following the framework (adopted `io.mcp-hangar.*` in #346).

**Key requirement:** Extensions MUST be disabled by default and require explicit opt-in by the client.

## Tasks (SEP-2663)

**Status:** merged into upstream spec `main`; the `2026-07-28` revision reshaped the wire.

**The trap:** the SDK's `mcp_types.Task*` models are a **frozen SEP-1686 fossil** — they never tracked the SEP-2663 reshape. Any forward-compat probe written against them can therefore never trip, which is how 2.0.0rc1 came to advertise a task surface that no spec client could actually drive.

**Our approach:** the task wire is **vendored** in `src/mcp_hangar/tasks_wire.py` rather than taken from the SDK types, per [ADR-015](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-015-vendored-task-wire.md). Relay-with-governance is [ADR-014](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-014-tasks-relay-with-governance.md), superseding the relay-only stance of ADR-008. The `relay_tasks_enabled` config switch remains the kill-switch on the serving surface.

## Upstream Release Position

**The dated release is cut.** Hangar speaks `2026-07-28` as its protocol revision: `src/mcp_hangar/protocol.py` → `SUPPORTED_PROTOCOL_VERSION = "2026-07-28"`, with legacy `2025-11-25` upstreams handled by downgrade in the `initialize` response and by the `_meta`-envelope compat path in `http_client.py`.

The following tracked SEPs had merged into upstream spec `main` by the 2026-07-08 audit: SEP-414, SEP-1865, SEP-2133, SEP-2243, SEP-2468, SEP-2549, SEP-2567, SEP-2575, SEP-2577, and SEP-2663 (Tasks).

Deliberately **not** implemented: `logging/setLevel`. SEP-2575 removes it and SEP-2577 deprecates Logging — the conformance suite flags it and that is expected. Do not "fix" it. See PRODUCT_ARCHITECTURE §9.

## ADR-005 Revisit Note (resolved)

ADR-005 framed interceptors as a core SEP. It is **Superseded by ADR-010** (the agent + cloud tier retirement). The in-process interceptor surface survived that retirement, and [ADR-012](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-012-interceptor-sep-pin-tracking-policy.md) is now the record governing its pin. No further action.

## Status Table

| Item | Ref | Status | Our Action |
|------|-----|--------|-----------|
| Interceptors spec | `experimental-ext-interceptors` (was SEP-1763, closed completed 2026-04-22) | Experimental extension repo; not core spec | Pin `2f66b9b` in `interceptor-pin-drift.yml`; deliberate-cadence bumps per ADR-012 |
| Digest pinning | [SEP-1766](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/1766) | Closed completed 2026-06-24; not merged into upstream spec | Keep `TaskDigestGuard` as own Validator |
| Extensions framework | SEP-2133 | Merged into upstream spec `main` by 2026-07-08 audit | Adopt reverse-DNS IDs; enforce default-off |
| Tasks | SEP-2663 | Merged; SDK `mcp_types.Task*` is a frozen SEP-1686 fossil | Vendored wire (`tasks_wire.py`, ADR-015); relay-with-governance (ADR-014) |
| Protocol revision | `2026-07-28` | Dated revision cut and adopted | `SUPPORTED_PROTOCOL_VERSION`; downgrade path for `2025-11-25` upstreams |
| `logging/setLevel` | SEP-2575 / SEP-2577 | Deliberately unimplemented | None — conformance flags it by design; do not implement |
| ADR-005 assumption review | ADR-005 | Resolved: superseded by ADR-010; pin policy now ADR-012 | None |
