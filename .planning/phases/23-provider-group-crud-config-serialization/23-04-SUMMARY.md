---
phase: 23-provider-group-crud-config-serialization
plan: "04"
subsystem: server/api
tags: [rest-api, crud, routing, cqrs-wiring]
dependency_graph:
  requires:
    - 23-01 (provider CRUD commands/handlers)
    - 23-02 (group CRUD handlers)
    - 23-03 (config serializer)
  provides:
    - POST/PUT/DELETE /api/providers/ endpoints
    - POST/PUT/DELETE /api/groups/ + member management endpoints
    - POST /api/config/export + POST /api/config/backup endpoints
    - register_crud_handlers() wired into init_cqrs()
  affects:
    - server/api/providers.py
    - server/api/groups.py
    - server/api/config.py
    - server/bootstrap/cqrs.py
tech_stack:
  added: []
  patterns:
    - Starlette Route + async handler functions
    - dispatch_command() async-to-sync bridge
    - run_in_threadpool for blocking serializer calls
    - yaml.safe_dump for YAML serialization
key_files:
  created:
    - tests/unit/test_api_crud_providers.py
    - tests/unit/test_api_crud_groups.py
    - tests/unit/test_api_crud_config.py
  modified:
    - mcp_hangar/server/api/providers.py
    - mcp_hangar/server/api/groups.py
    - mcp_hangar/server/api/config.py
    - mcp_hangar/server/bootstrap/cqrs.py
decisions:
  - UpdateGroupCommand has no strategy field; removed strategy from update_group handler (matches Plan 02 design)
  - Config routes ordered /export and /backup before /reload to avoid path conflicts
  - register_crud_handlers() imported lazily inside init_cqrs() to mirror existing PROVIDER_REPOSITORY import pattern
metrics:
  duration: ~25 minutes
  completed: 2026-03-22
  tasks_completed: 3
  files_modified: 7
---

# Phase 23 Plan 04: REST Endpoint Wiring Summary

**One-liner:** Wired 10 new REST endpoints (provider/group CRUD + config export/backup) into Starlette route modules and registered all CRUD handlers in `init_cqrs()`.

## What Was Built

Three tasks completed using TDD (RED-GREEN-REFACTOR):

### Task 1: Provider CRUD REST endpoints
- `POST /api/providers/` → `CreateProviderCommand` → 201
- `PUT /api/providers/{id}` → `UpdateProviderCommand` → 200
- `DELETE /api/providers/{id}` → `DeleteProviderCommand` → 200
- 15 unit tests, all passing

### Task 2: Group CRUD + member management REST endpoints
- `POST /api/groups/` → `CreateGroupCommand` → 201
- `PUT /api/groups/{id}` → `UpdateGroupCommand` → 200
- `DELETE /api/groups/{id}` → `DeleteGroupCommand` → 200
- `POST /api/groups/{id}/members` → `AddGroupMemberCommand` → 201 (body: `member_id` mapped to `provider_id`)
- `DELETE /api/groups/{id}/members/{member_id}` → `RemoveGroupMemberCommand` → 200
- 24 unit tests, all passing

### Task 3: Config export/backup + CQRS bootstrap wiring
- `POST /api/config/export` → `serialize_full_config()` + `yaml.safe_dump` → 200 `{"yaml": str}`
- `POST /api/config/backup` → `write_config_backup(config_path)` → 200 `{"path": str}`
- `init_cqrs()` now calls `register_crud_handlers(command_bus, repository, event_bus, GROUPS)`
- 11 unit tests, all passing

**Total: 50 tests across 3 files, all passing. 0 ruff errors.**

## Commits

| Task | Phase | Commit | Description |
|------|-------|--------|-------------|
| 1 | RED | d9c173f | test(23-04): add failing tests for provider CRUD REST endpoints |
| 1 | GREEN | b81c00b | feat(23-04): add provider CRUD REST endpoints (create, update, delete) |
| 2 | RED | fdf2eb7 | test(23-04): add failing tests for group CRUD REST endpoints |
| 2 | GREEN | 43a1846 | feat(23-04): add group CRUD + member management REST endpoints |
| 3 | RED | 564403b | test(23-04): add failing tests for config export/backup REST endpoints |
| 3 | GREEN | c0a9632 | feat(23-04): add config export/backup endpoints and wire register_crud_handlers |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `UpdateGroupCommand` has no `strategy` field**
- **Found during:** Task 2 GREEN phase (TypeError at test runtime)
- **Issue:** Plan 04 spec showed `strategy` being passed to `UpdateGroupCommand`, but Plan 02 intentionally omitted `strategy` from `UpdateGroupCommand` (only `description` and `min_healthy` are mutable post-creation)
- **Fix:** Removed `strategy=body.get("strategy")` line from the `update_group` handler
- **Files modified:** `mcp_hangar/server/api/groups.py`
- **Commit:** 43a1846

## Self-Check: PASSED

All 7 source/test files exist. All 6 task commits verified in git log.
