---
phase: 11-backend-rest-api
plan: "04"
subsystem: server/api + server/bootstrap
tags: [rest-api, cqrs, auth, api-keys, roles, bootstrap, tdd, gap-closure]
dependency_graph:
  requires: [api-foundation, auth-bootstrap, auth-cqrs-handlers]
  provides: [auth-rest-endpoints, init_auth_cqrs]
  affects: [server/api/router.py, server/bootstrap/cqrs.py, server/bootstrap/__init__.py]
tech_stack:
  added: []
  patterns: [tdd-red-green-refactor, cqrs-command-handler, cqrs-query-handler, run_in_threadpool-async-bridge, starlette-routes]
key_files:
  created:
    - packages/core/mcp_hangar/server/api/auth.py
    - packages/core/tests/unit/test_api_auth.py
  modified:
    - packages/core/mcp_hangar/server/api/router.py
    - packages/core/mcp_hangar/server/bootstrap/cqrs.py
    - packages/core/mcp_hangar/server/bootstrap/__init__.py
decisions:
  - "init_auth_cqrs checks getattr(auth_components, 'enabled', False) rather than just is None -- auth may be disabled even when AuthComponents object exists"
  - "TestClient.delete() does not support json= kwarg in this Starlette version -- tests use api_client.request('DELETE', url, json=...) pattern"
  - "revoke_api_key body parsing uses try/except to handle missing body gracefully -- DELETE body is optional per HTTP spec"
metrics:
  duration: "15 minutes"
  completed: "2026-03-14T16:00:00Z"
  tasks_completed: 2
  files_created: 2
  files_modified: 3
---

# Phase 11 Plan 04: Auth REST Endpoints and CQRS Bootstrap Wiring Summary

Auth management REST endpoints (API keys + roles) exposed at /api/auth, with CQRS handlers wired into bootstrap via init_auth_cqrs() so dispatch_command/dispatch_query reach them.

## What Was Built

### Task 1: Add init_auth_cqrs to bootstrap/cqrs.py and call from bootstrap/__init__.py

__Function `init_auth_cqrs(runtime, auth_components)`:__

- Added to `server/bootstrap/cqrs.py` after `init_cqrs()`
- Registers auth command handlers (CreateApiKey, RevokeApiKey, ListApiKeys, AssignRole, RevokeRole, CreateCustomRole) on `runtime.command_bus`
- Registers auth query handlers (GetApiKeysByPrincipal, ListBuiltinRoles, GetRolesForPrincipal, CheckPermission) on `runtime.query_bus`
- Skips silently if auth is disabled (`not getattr(auth_components, "enabled", False)`) or `auth_components is None`
- Passes `api_key_store` and `role_store` from AuthComponents to handler registration functions

__Bootstrap wiring (`bootstrap/__init__.py`):__

- Added `init_auth_cqrs` to import from `.cqrs`
- Added call `init_auth_cqrs(runtime, auth_components)` immediately after `bootstrap_auth(...)`
- Added to `__all__` for public API

### Task 2: Create server/api/auth.py and mount at /auth in router

__Auth endpoints (`server/api/auth.py`):__

8 handlers, all routing through `dispatch_command` / `dispatch_query`:

| Method | Path | Command/Query | Status |
|--------|------|---------------|--------|
| POST | /auth/keys | CreateApiKeyCommand | 201 |
| DELETE | /auth/keys/{key_id} | RevokeApiKeyCommand | 200 |
| GET | /auth/keys | GetApiKeysByPrincipalQuery | 200 |
| POST | /auth/roles | CreateCustomRoleCommand | 201 |
| GET | /auth/roles | ListBuiltinRolesQuery | 200 |
| POST | /auth/roles/assign | AssignRoleCommand | 200 |
| DELETE | /auth/roles/revoke | RevokeRoleCommand | 200 |
| GET | /auth/principals/roles | GetRolesForPrincipalQuery | 200 |

__Router update (`server/api/router.py`):__

- Added `Mount("/auth", routes=auth_routes)` to `create_api_router()`
- Added import: `from .auth import auth_routes`

__Tests (`tests/unit/test_api_auth.py`):__

- 22 unit tests covering all 8 endpoints
- Patches `dispatch_command` and `dispatch_query` in `mcp_hangar.server.api.auth`
- Validates request routing, status codes, response shape, and query/command parameter forwarding
- Uses `api_client.request("DELETE", url, json=...)` pattern for DELETE requests with JSON body (Starlette TestClient limitation)

## Deviations from Plan

### Auto-fixed Issues

__1. [Rule 1 - Bug] Fixed init_auth_cqrs disabled-auth check__

- __Found during:__ Task 1 implementation
- __Issue:__ Initial implementation used `auth_components is None` as the only skip condition. But `AuthComponents` can exist with `enabled=False` (e.g., auth feature present but disabled in config), which would cause auth handlers to register against None stores.
- __Fix:__ Changed guard to `auth_components is None or not getattr(auth_components, "enabled", False)` to skip whenever auth is not actively enabled.
- __Files modified:__ `packages/core/mcp_hangar/server/bootstrap/cqrs.py`
- __Commit:__ 11c5e19

__2. [Rule 3 - Blocking] Worked around TestClient.delete() limitation__

- __Found during:__ Task 2 test writing
- __Issue:__ `TestClient.delete(url, json=...)` raises `TypeError` in this version of Starlette -- the shorthand DELETE method does not accept `json=` or `content=` kwargs.
- __Fix:__ Used `api_client.request("DELETE", url, json=...)` pattern for all DELETE tests requiring a JSON body, consistent with what works in this Starlette version.
- __Files modified:__ `packages/core/tests/unit/test_api_auth.py`
- __Commit:__ 5bfed86

## Verification

All success criteria confirmed:

- `server/api/auth.py` exists with `auth_routes` list (8 routes)
- `init_auth_cqrs` importable from `server/bootstrap/cqrs.py`
- `server/bootstrap/__init__.py` calls `init_auth_cqrs(runtime, auth_components)` after `bootstrap_auth`
- `server/api/router.py` mounts `/auth` (confirmed via `create_api_router()` route listing)
- All 22 unit tests pass in `tests/unit/test_api_auth.py`

## Self-Check: PASSED

Files confirmed present:

- `packages/core/mcp_hangar/server/api/auth.py` -- FOUND
- `packages/core/mcp_hangar/server/api/router.py` -- FOUND
- `packages/core/mcp_hangar/server/bootstrap/cqrs.py` -- FOUND
- `packages/core/mcp_hangar/server/bootstrap/__init__.py` -- FOUND
- `packages/core/tests/unit/test_api_auth.py` -- FOUND

Commits confirmed present:

- `11c5e19` -- feat(11-04): add init_auth_cqrs to bootstrap/cqrs.py and wire into bootstrap
- `5bfed86` -- feat(11-04): create server/api/auth.py and mount /auth in router
