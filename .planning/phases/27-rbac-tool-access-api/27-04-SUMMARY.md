---
phase: 27-rbac-tool-access-api
plan: 04
subsystem: server/bootstrap
tags: [bootstrap, wiring, tap, rbac, context]
dependency_graph:
  requires: [27-01, 27-02, 27-03]
  provides: [tap-bootstrap, startup-replay, context-properties]
  affects:
    - mcp_hangar/server/auth_bootstrap.py
    - mcp_hangar/server/bootstrap/cqrs.py
    - mcp_hangar/server/context.py
tech_stack:
  added: []
  patterns: [composition-root, startup-replay, null-object, property-accessor]
key_files:
  modified:
    - mcp_hangar/server/auth_bootstrap.py
    - mcp_hangar/server/bootstrap/cqrs.py
    - mcp_hangar/server/context.py
decisions:
  - tap_store created only for sqlite driver; memory driver gets no persistence
  - _replay_tap_policies() called before bootstrap_auth() returns to ensure resolver is warm before requests
  - ApplicationContext.tap_store raises AttributeError when auth_components absent
metrics:
  duration: ~20min
  completed: 2026-03-22
  tasks_completed: 1
  files_changed: 3
---

# Phase 27 Plan 04: Bootstrap Wiring Summary

**One-liner:** tap_store added to AuthComponents, SQLiteToolAccessPolicyStore created on startup with policy replay into ToolAccessResolver, init_auth_cqrs updated to pass tap_store and event_bus.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1    | Wire tap_store through auth bootstrap, cqrs registration, and context | ef9686d |

## What Was Built

### auth_bootstrap.py

- `AuthComponents` dataclass gains `tap_store: IToolAccessPolicyStore | None` field
- `_create_storage_backends()` returns `(api_key_store, role_store, tap_store)` triple
- `SQLiteToolAccessPolicyStore` instantiated for sqlite driver
- `_replay_tap_policies(tap_store, resolver)` iterates `tap_store.list_all_policies()` and calls the correct resolver method per scope
- `bootstrap_auth()` calls `_replay_tap_policies()` before returning

### cqrs.py

- `init_auth_cqrs()` updated to pass `tap_store=getattr(auth_components, "tap_store", None)` and `event_bus` to `register_auth_command_handlers()`

### context.py

- `ApplicationContext` gains `auth_components: AuthComponents | None` field
- `role_store` property returns `self._auth_components.role_store`
- `tap_store` property returns `self._auth_components.tap_store`

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- `mcp_hangar/server/auth_bootstrap.py` contains `def _replay_tap_policies`
- `mcp_hangar/server/context.py` contains `def role_store`
- Commit ef9686d present in git log
- All 2721 unit tests passed after changes
