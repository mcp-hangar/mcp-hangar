---
phase: 27-rbac-tool-access-api
plan: "02"
subsystem: application-cqrs
tags: [rbac, tool-access-policy, cqrs, commands, queries, handlers]
dependency_graph:
  requires:
    - Plan 27-01 (domain exceptions, events, IRoleStore extensions, IToolAccessPolicyStore, SQLiteToolAccessPolicyStore)
  provides:
    - DeleteCustomRoleCommand, UpdateCustomRoleCommand, SetToolAccessPolicyCommand, ClearToolAccessPolicyCommand
    - ListAllRolesQuery, ListPrincipalsQuery, GetToolAccessPolicyQuery
    - DeleteCustomRoleHandler, UpdateCustomRoleHandler, SetToolAccessPolicyHandler, ClearToolAccessPolicyHandler
    - Backfilled CreateCustomRoleHandler with event_bus support and CustomRoleCreated emission
    - ListAllRolesHandler, ListPrincipalsHandler, GetToolAccessPolicyHandler
    - Updated register_auth_command_handlers (tap_store + event_bus params)
    - Updated register_auth_query_handlers (tap_store param)
  affects:
    - mcp_hangar/application/commands/auth_commands.py
    - mcp_hangar/application/commands/auth_handlers.py
    - mcp_hangar/application/queries/auth_queries.py
    - mcp_hangar/application/queries/auth_handlers.py
    - tests/unit/test_auth_command_handlers.py
    - tests/unit/test_auth_query_handlers.py
tech_stack:
  added: []
  patterns:
    - CQRS command/query handler pattern with frozen dataclasses
    - Event bus publish pattern for domain event emission
    - Lazy import of get_tool_access_resolver inside handler to avoid circular deps
key_files:
  created: []
  modified:
    - mcp_hangar/application/commands/auth_commands.py
    - mcp_hangar/application/commands/auth_handlers.py
    - mcp_hangar/application/queries/auth_queries.py
    - mcp_hangar/application/queries/auth_handlers.py
    - tests/unit/test_auth_command_handlers.py
    - tests/unit/test_auth_query_handlers.py
decisions:
  - get_tool_access_resolver() imported lazily inside SetToolAccessPolicyHandler.handle() to avoid circular import
  - ListPrincipalsHandler uses hasattr guard to support both InMemoryRoleStore (_assignments) and SQLiteRoleStore (list_principals())
  - CreateCustomRoleHandler event_bus=None default so existing callers without event_bus still work
metrics:
  duration: ~10 min
  completed: "2026-03-22"
---

# Phase 27 Plan 02: CQRS Commands and Query Handlers Summary

**One-liner:** 4 new command dataclasses, 3 query dataclasses, 7 new handlers, and updated registration functions wiring RBAC + Tool Access Policy management into the CQRS buses.

## What Was Built

Extended the CQRS command and query layers with all Phase 27 commands, queries, and handlers. Plans 03 and 04 can now dispatch these types at the REST layer.

### Task 1: New Command and Query Dataclasses

Added to `auth_commands.py`:
- `DeleteCustomRoleCommand(role_name, deleted_by)` — frozen dataclass
- `UpdateCustomRoleCommand(role_name, permissions, description, updated_by)` — frozen, permissions as `list[str]`
- `SetToolAccessPolicyCommand(scope, target_id, allow_list, deny_list)` — frozen, all lists default to empty
- `ClearToolAccessPolicyCommand(scope, target_id)` — frozen

Added to `auth_queries.py`:
- `ListAllRolesQuery(include_builtin=True)` — frozen
- `ListPrincipalsQuery()` — frozen, no fields
- `GetToolAccessPolicyQuery(scope, target_id)` — frozen

### Task 2: Command Handlers

Updated `auth_handlers.py` (commands):
- `CreateCustomRoleHandler` — backfilled with `event_bus: Any = None`; now publishes `CustomRoleCreated` after `add_role()`
- `DeleteCustomRoleHandler` — calls `role_store.delete_role()` (raises on builtin/not found), publishes `CustomRoleDeleted`
- `UpdateCustomRoleHandler` — calls `role_store.update_role()`, publishes `CustomRoleUpdated`
- `SetToolAccessPolicyHandler` — persists to `tap_store`, updates `ToolAccessResolver` singleton, publishes `ToolAccessPolicySet`
- `ClearToolAccessPolicyHandler` — removes from `tap_store`, publishes `ToolAccessPolicyCleared`
- `register_auth_command_handlers` — updated signature: `tap_store=None, event_bus=None`

### Task 3: Query Handlers

Updated `auth_handlers.py` (queries):
- `ListAllRolesHandler` — combines builtin roles (from `BUILTIN_ROLES` dict) with custom roles from `role_store.list_all_roles()`
- `ListPrincipalsHandler` — uses `list_principals()` if available (SQLiteRoleStore), falls back to `_assignments` (InMemoryRoleStore)
- `GetToolAccessPolicyHandler` — returns `{"found": False}` when no policy, or policy data when found
- `register_auth_query_handlers` — updated signature: `tap_store=None`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test registration counts**
- **Found during:** Task 2/3 verification
- **Issue:** Pre-existing tests asserted `command_bus.register.call_count == 6` and `query_bus.register.call_count == 6`; now 8 each due to new handlers
- **Fix:** Updated expected counts and comments in both test files
- **Files modified:** `tests/unit/test_auth_command_handlers.py`, `tests/unit/test_auth_query_handlers.py`
- **Commit:** ca7dced

## Verification

- All imports pass: all 4 new commands, all 3 new queries, all 7 new handlers
- `ruff check` — clean
- Full unit test suite: **2721 passed, 1 skipped** — zero regressions

## Commits

| Hash    | Message                                                           |
| ------- | ----------------------------------------------------------------- |
| ca7dced | feat(27-02): add CQRS commands and query handlers for RBAC + TAP management |

## Self-Check: PASSED

- All 4 command dataclasses importable from `auth_commands.py` — confirmed
- All 3 query dataclasses importable from `auth_queries.py` — confirmed
- All 7 new handlers importable from respective handler files — confirmed
- Commit ca7dced in git log — confirmed
