---
phase: 27-rbac-tool-access-api
plan: 03
subsystem: server/api
tags: [rest-api, rbac, tap, auth, routing]
dependency_graph:
  requires: [27-02]
  provides: [rbac-rest-endpoints, tap-rest-endpoints]
  affects: [mcp_hangar/server/api/auth.py]
tech_stack:
  added: []
  patterns: [starlette-routing, scope-validation, dispatch-command, dispatch-query]
key_files:
  modified:
    - mcp_hangar/server/api/auth.py
decisions:
  - GET /auth/policies returns 200 with found=false rather than 404 for absent policies
  - scope validation guard (_VALID_TAP_SCOPES) returns 400 before dispatching command
  - exact-match routes placed before parameterised routes to prevent shadowing
metrics:
  duration: ~15min
  completed: 2026-03-22
  tasks_completed: 1
  files_changed: 1
---

# Phase 27 Plan 03: REST API Endpoints Summary

**One-liner:** 10 new async route handlers for RBAC custom role CRUD and Tool Access Policy endpoints wired into Starlette auth_routes.

## Tasks Completed

| Task | Description | Commit |
|------|-------------|--------|
| 1    | Add 10 REST handlers and update auth_routes (18 routes total) | 52121f0 |

## What Was Built

Added 10 new route handler functions to `mcp_hangar/server/api/auth.py`:

- `list_all_roles` — GET /auth/roles/all
- `get_role` — GET /auth/roles/{role_name}
- `delete_role` — DELETE /auth/roles/{role_name} (204)
- `update_role` — PATCH /auth/roles/{role_name}
- `list_principals` — GET /auth/principals
- `list_permissions` — GET /auth/permissions (static, no bus dispatch)
- `check_permission` — POST /auth/check-permission
- `set_tool_access_policy` — POST /auth/policies/{scope}/{target_id}
- `get_tool_access_policy` — GET /auth/policies/{scope}/{target_id}
- `clear_tool_access_policy` — DELETE /auth/policies/{scope}/{target_id} (204)

`auth_routes` updated to 18 routes with correct ordering (exact-match before parameterised).

`_VALID_TAP_SCOPES = frozenset({"provider", "group", "member"})` added as module-level guard.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- `mcp_hangar/server/api/auth.py` exists and contains all 10 new handlers
- Commit 52121f0 present in git log
- All 22 pre-existing unit tests passed after changes
