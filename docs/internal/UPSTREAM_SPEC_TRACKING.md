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

## `Mcp-Param-*` Header Validation (SEP-2243)

**Upstream ref:** SEP-2243 (header parameters and header-body agreement), implemented in the SDK transport at `mcp/server/_streamable_http_modern.py`.

**Why it is pinned:** the SDK enforces header-body agreement pre-dispatch and **fails open by design** — when it cannot resolve the called tool's schema, validation is skipped and the call proceeds. Hangar mirrors that precondition in two places rather than wrapping the transport: `_call_carries_param_check` (`flat_tool_projection.py`) decides when a skip was owed and therefore worth counting, and the skip status then gates the L7 header selector ([ADR-025](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-025-header-selectors-must-not-match-unvalidated-headers.md)). That is a copy of another project's control flow: true for `mcp==2.0.0`, unverified for anything else. An SDK bump that moves the ladder does not break the build — it makes the metric, and the gate, quietly wrong.

**Our action:** an `mcp` upgrade re-diffs `_tool_input_schema` and `_mcp_param_rejection` against the mirrored branches, **in the same change as the bump**, per the [ADR-012](https://github.com/mcp-hangar/docs/blob/main/adr/ADR-012-interceptor-sep-pin-tracking-policy.md) pin policy. The re-diff covers every skip condition below, including the ones Hangar cannot reach today — a mirror of four conditions out of seven passes its own re-diff green while the SDK changes one of the three nobody mirrors, which is the failure the pin exists to prevent.

| Skip condition | Where | Metric reason | Reachable through Hangar |
|---|---|---|---|
| Listing raised | `_tool_input_schema` | `listing_failed` | Yes — the live gap ADR-025 closes |
| Listing exhausted without the tool | `_tool_input_schema` | `tool_not_listed` | Yes; dispatch answers `-32601`, so not an execution gap |
| Tool's annotations invalid | `validate_mcp_param_headers` (`shared/inbound.py`) | `invalid_annotation` | Effectively no — `#1063` withholds such a tool from the projection, and no `hangar_*` schema declares `x-mcp-header` |
| Legacy revision (ladder not entered) | — | `legacy_protocol` | Yes; the selector refuses to match on it |
| Cursor cycle | `_tool_input_schema` | **none** | No — the front door returns one unpaged list |
| Pagination past the page cap | `_tool_input_schema` | **none** | No — same reason |
| Envelope fails `tools/list` validation | `_tool_input_schema` | **none** | Out of scope: a client fault dispatch rejects on its own |

**Before paginating the front-door projection:** the last two rows stop being unreachable the moment the projection emits a `nextCursor`. Their reason labels must be added *in the change that paginates*, or it opens a skip that nothing counts and the selector gate cannot see.

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
| `Mcp-Param-*` validation | SEP-2243 | Merged; SDK ladder is fail-open by design | Mirrored preconditions pinned at `mcp==2.0.0`; re-diff both functions on an SDK bump (ADR-012, ADR-025) |
| Tasks | SEP-2663 | Merged; SDK `mcp_types.Task*` is a frozen SEP-1686 fossil | Vendored wire (`tasks_wire.py`, ADR-015); relay-with-governance (ADR-014) |
| Protocol revision | `2026-07-28` | Dated revision cut and adopted | `SUPPORTED_PROTOCOL_VERSION`; downgrade path for `2025-11-25` upstreams |
| `logging/setLevel` | SEP-2575 / SEP-2577 | Deliberately unimplemented | None — conformance flags it by design; do not implement |
| ADR-005 assumption review | ADR-005 | Resolved: superseded by ADR-010; pin policy now ADR-012 | None |
