---
phase: 27-rbac-tool-access-api
plan: 05
subsystem: tests/unit
tags: [tests, rbac, tap, auth, rest-api]
dependency_graph:
  requires: [27-01, 27-02, 27-03, 27-04]
  provides: [phase-27-test-coverage]
  affects:
    - tests/unit/test_auth_storage.py
    - tests/unit/test_auth_command_handlers.py
    - tests/unit/test_api_auth.py
    - tests/unit/test_tap_store.py
    - tests/unit/test_tap_handlers.py
tech_stack:
  added: []
  patterns: [pytest-fixtures, unittest-mock, asyncmock, starlette-testclient]
key_files:
  created:
    - tests/unit/test_tap_store.py
    - tests/unit/test_tap_handlers.py
  modified:
    - tests/unit/test_auth_storage.py
    - tests/unit/test_auth_command_handlers.py
    - tests/unit/test_api_auth.py
decisions:
  - get_tool_access_policy returns 200 with found=false rather than 404 for absent policies
  - check_permission handler accepts combined permission string (resource:action:id) or split fields
  - list_permissions import moved inside try block so ImportError is caught by fallback
  - test_tap_handlers mocks domain module path not local-import module path
metrics:
  duration: ~30min
  completed: 2026-03-22
  tasks_completed: 5
  files_changed: 5
---

# Phase 27 Plan 05: Tests Summary

**One-liner:** Full unit test suite for Phase 27 — 5 files, ~90 new tests covering SQLiteRoleStore new methods, SQLiteToolAccessPolicyStore, TAP and RBAC command/query handlers, and all 10 new REST endpoints.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1-4  | test_auth_storage, test_tap_store, test_auth_command_handlers, test_tap_handlers | (previous session) |
| 5    | Append 10 REST test classes to test_api_auth.py + fix 3 handler edge cases | 0344201 |

## What Was Built

### New test files

**tests/unit/test_tap_store.py** (11 tests): SQLiteToolAccessPolicyStore set/get/clear/list round-trips, None for absent policy, overwrite semantics.

**tests/unit/test_tap_handlers.py** (19 tests): SetToolAccessPolicyHandler (provider/group/member scope routing, event emission, store persistence), ClearToolAccessPolicyHandler (store call, event emission), GetToolAccessPolicyHandler (found/not-found), ListAllRolesHandler, ListPrincipalsHandler.

### Extended test files

**tests/unit/test_auth_storage.py**: Appended `TestSQLiteRoleStoreNewMethods` (9 tests) — list_all_roles, delete_role (builtin guard, not-found guard), update_role.

**tests/unit/test_auth_command_handlers.py**: Replaced `TestCreateCustomRoleHandler` with event-emission tests, replaced `TestRegisterAuthCommandHandlers` with updated call counts, appended `TestDeleteCustomRoleHandler` and `TestUpdateCustomRoleHandler`.

**tests/unit/test_api_auth.py**: Appended 10 new test classes (44 total in file) covering all Phase 27 REST endpoints.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] list_permissions import inside try block**
- **Found during:** Task 5 — TestListPermissions.test_returns_200 failing
- **Issue:** `from ...domain.value_objects.security import ACTIONS, RESOURCE_TYPES` was outside the try block, so ImportError propagated instead of falling back to hardcoded defaults
- **Fix:** Moved import inside try block
- **Files modified:** `mcp_hangar/server/api/auth.py`
- **Commit:** 0344201

**2. [Rule 1 - Bug] check_permission handler requires split fields**
- **Found during:** Task 5 — TestCheckPermission.test_returns_200 failing with KeyError
- **Issue:** Handler required separate `action` + `resource_type` fields but plan specified combined `permission` string format
- **Fix:** Handler now accepts either `action`/`resource_type` or combined `permission` string with `:` splitting
- **Files modified:** `mcp_hangar/server/api/auth.py`
- **Commit:** 0344201

**3. [Rule 1 - Bug] get_tool_access_policy returned 404 for absent policy**
- **Found during:** Task 5 — TestGetToolAccessPolicy.test_returns_200 failing with 404
- **Issue:** Handler returned 404 when `found=false` but plan behavior description specified 200 with `found` field in body
- **Fix:** Removed 404 branch; handler always returns 200 with the query result (which includes `found: false`)
- **Files modified:** `mcp_hangar/server/api/auth.py`
- **Commit:** 0344201

**4. [Rule 1 - Bug] test_tap_handlers mock target incorrect**
- **Found during:** Task 4/5 — TestSetToolAccessPolicyHandler.test_returns_confirmation failing with AttributeError
- **Issue:** Tests patched `mcp_hangar.application.commands.auth_handlers.get_tool_access_resolver` but the import is local (inside handler body), so patch target must be the original domain module
- **Fix:** Changed mock target to `mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver`
- **Files modified:** `tests/unit/test_tap_handlers.py`
- **Commit:** 0344201

## Self-Check: PASSED

- `tests/unit/test_tap_store.py` exists
- `tests/unit/test_tap_handlers.py` exists
- `tests/unit/test_api_auth.py` contains `class TestListAllRoles`
- Commit 0344201 present in git log
- Full suite: **2788 passed, 1 skipped** — zero failures
