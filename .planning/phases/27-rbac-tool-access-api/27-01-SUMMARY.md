---
phase: 27-rbac-tool-access-api
plan: "01"
subsystem: domain-infrastructure
tags: [rbac, tool-access-policy, domain-events, exceptions, sqlite, protocol]
dependency_graph:
  requires: []
  provides:
    - RoleNotFoundError and CannotModifyBuiltinRoleError (domain/exceptions.py)
    - 5 new domain events (CustomRoleCreated, CustomRoleDeleted, CustomRoleUpdated, ToolAccessPolicySet, ToolAccessPolicyCleared)
    - Extended IRoleStore protocol with list_all_roles, delete_role, update_role
    - New IToolAccessPolicyStore protocol
    - SQLiteRoleStore.list_all_roles, delete_role, update_role implementations
    - InMemoryRoleStore stubs for same methods
    - EventSourcedRoleStore stubs for same methods (unplanned, protocol compliance)
    - SQLiteToolAccessPolicyStore (new file)
  affects:
    - mcp_hangar/domain/exceptions.py
    - mcp_hangar/domain/events.py
    - mcp_hangar/domain/contracts/authorization.py
    - mcp_hangar/infrastructure/auth/sqlite_store.py
    - mcp_hangar/infrastructure/auth/rbac_authorizer.py
    - mcp_hangar/infrastructure/auth/event_sourced_store.py
    - mcp_hangar/infrastructure/auth/sqlite_tap_store.py (new)
tech_stack:
  added: []
  patterns:
    - SQLite thread-local connection pattern (mirroring SQLiteRoleStore)
    - Protocol structural subtyping for IRoleStore and IToolAccessPolicyStore
    - Domain event pattern with @dataclass + __post_init__: super().__init__()
key_files:
  created:
    - mcp_hangar/infrastructure/auth/sqlite_tap_store.py
  modified:
    - mcp_hangar/domain/exceptions.py
    - mcp_hangar/domain/events.py
    - mcp_hangar/domain/contracts/authorization.py
    - mcp_hangar/infrastructure/auth/sqlite_store.py
    - mcp_hangar/infrastructure/auth/rbac_authorizer.py
    - mcp_hangar/infrastructure/auth/event_sourced_store.py
decisions:
  - ToolAccessPolicy uses tuple[str, ...] for allow_list/deny_list — sqlite_tap_store constructs with tuple(json.loads(...))
  - BUILTIN_ROLES guard imported from domain/security/roles.py — checked before any delete/update
  - EventSourcedRoleStore stub implementations added (unplanned) to restore protocol compliance
metrics:
  duration: ~15 min
  completed: "2026-03-22"
---

# Phase 27 Plan 01: Domain & Infrastructure Foundation Summary

**One-liner:** Domain exceptions, events, extended IRoleStore/IToolAccessPolicyStore protocols, and SQLiteToolAccessPolicyStore for persistent tool access policy storage.

## What Was Built

Laid the complete domain and infrastructure foundation for Phase 27 RBAC + Tool Access Policy API. All five prerequisite artifacts are now in place so Wave 2 plans can build against stable contracts.

### Task 1: Domain Exceptions and Events

Added to `mcp_hangar/domain/exceptions.py`:
- `RoleNotFoundError(role_name)` — inherits from `AuthorizationError`, message: `"Role not found: {role_name}"`
- `CannotModifyBuiltinRoleError(role_name)` — inherits from `AuthorizationError`, message: `"Cannot modify built-in role: {role_name}"`

Added to `mcp_hangar/domain/events.py`:
- `CustomRoleCreated(role_name, permissions, description)` — emitted when custom role is created
- `CustomRoleDeleted(role_name)` — emitted when custom role is deleted
- `CustomRoleUpdated(role_name, permissions, description)` — emitted when custom role is updated
- `ToolAccessPolicySet(scope, target_id, allow_list, deny_list)` — emitted when TAP is upserted
- `ToolAccessPolicyCleared(scope, target_id)` — emitted when TAP is removed

### Task 2: Extended Protocol Contracts

Extended `IRoleStore` in `mcp_hangar/domain/contracts/authorization.py` with:
- `list_all_roles() -> list[Role]`
- `delete_role(role_name: str) -> None`
- `update_role(role_name, permissions, description) -> Role`

Added new `IToolAccessPolicyStore` protocol with:
- `set_policy(scope, target_id, allow_list, deny_list) -> None`
- `get_policy(scope, target_id) -> ToolAccessPolicy | None`
- `clear_policy(scope, target_id) -> None`
- `list_all_policies() -> list[tuple[str, str, list[str], list[str]]]`

### Task 3: Implementations and New Store

- `SQLiteRoleStore`: added `list_all_roles` (queries `is_builtin=0`), `delete_role` (guards builtin, raises `RoleNotFoundError`), `update_role` (guards builtin, updates row, returns `Role`)
- `InMemoryRoleStore`: added same three methods with in-memory `_roles` dict and `_assignments` cleanup
- Created `mcp_hangar/infrastructure/auth/sqlite_tap_store.py` — 160-line `SQLiteToolAccessPolicyStore` with WAL mode, thread-local connections, full set/get/clear/list round-trip

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] EventSourcedRoleStore protocol compliance**
- **Found during:** Task 3 (test suite run)
- **Issue:** Extending `IRoleStore` with 3 new `@abstractmethod` methods made `EventSourcedRoleStore` non-instantiable — Python raised `TypeError: Can't instantiate abstract class`
- **Fix:** Added stub implementations of `list_all_roles`, `delete_role`, `update_role` to `EventSourcedRoleStore` in `mcp_hangar/infrastructure/auth/event_sourced_store.py`
- **Files modified:** `mcp_hangar/infrastructure/auth/event_sourced_store.py`
- **Commit:** 648d9f9

## Verification

- All imports pass: `RoleNotFoundError`, `CannotModifyBuiltinRoleError`, 5 new events, `IRoleStore`, `IToolAccessPolicyStore`, `SQLiteToolAccessPolicyStore`
- `ruff check mcp_hangar` — clean
- Full test suite: **2861 passed, 39 skipped** — zero regressions

## Commits

| Hash    | Message                                                           |
| ------- | ----------------------------------------------------------------- |
| 67ee346 | feat(27-01): add domain exceptions, events, and extended authorization contracts |
| 648d9f9 | feat(27-01): implement IRoleStore extensions and SQLiteToolAccessPolicyStore |

## Self-Check: PASSED

- `mcp_hangar/infrastructure/auth/sqlite_tap_store.py` — exists
- `mcp_hangar/domain/exceptions.py` contains `RoleNotFoundError` — confirmed
- `mcp_hangar/domain/events.py` contains `CustomRoleCreated` — confirmed
- `mcp_hangar/domain/contracts/authorization.py` contains `IToolAccessPolicyStore` — confirmed
- Commits 67ee346 and 648d9f9 — both in git log
