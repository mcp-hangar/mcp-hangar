---
phase: 11-backend-rest-api
plan: "02"
subsystem: server/api
tags: [rest-api, starlette, cqrs, groups, discovery, config, system, tdd]
dependency_graph:
  requires: [api-foundation, provider-endpoints]
  provides: [group-endpoints, discovery-endpoints, config-endpoints, system-endpoints]
  affects: [server/api/router.py]
tech_stack:
  added: []
  patterns: [starlette-routing, cqrs-dispatch, tdd-red-green-refactor]
key_files:
  created:
    - packages/core/mcp_hangar/server/api/groups.py
    - packages/core/mcp_hangar/server/api/discovery.py
    - packages/core/mcp_hangar/server/api/config.py
    - packages/core/mcp_hangar/server/api/system.py
    - packages/core/tests/unit/test_api_groups.py
    - packages/core/tests/unit/test_api_discovery.py
    - packages/core/tests/unit/test_api_config_system.py
  modified:
    - packages/core/mcp_hangar/server/api/router.py
decisions:
  - "ProviderGroup serialized via to_status_dict() for consistent member representation"
  - "DiscoveryNotConfigured extends ProviderNotFoundError to inherit HTTP 404 mapping from middleware"
  - "system.py uses only dispatch_query (no get_context import) since it does not need direct context access"
  - "config.py strips sensitive keys at top level only (non-recursive) as a defense-in-depth measure"
metrics:
  duration: "10 minutes"
  completed: "2026-03-14T15:01:35Z"
  tasks_completed: 2
  files_created: 7
  files_modified: 1
---

# Phase 11 Plan 02: Groups, Discovery, Config, and System REST Endpoints Summary

REST endpoints for provider groups, discovery management, configuration, and system info implemented with TDD — completing the full API surface for phase 11.

## What Was Built

### Task 1: Group and Discovery Endpoints (TDD)

**Groups (`GET /groups/`, `GET /groups/{group_id}`):**

- Lists all provider groups from `ApplicationContext.groups` dict
- Returns each group serialized via `ProviderGroup.to_status_dict()`
- Returns 404 for unknown group IDs

**Discovery (`GET /discovery/pending`, `GET /discovery/quarantined`, `POST /discovery/approve/{name}`, `POST /discovery/reject/{name}`, `GET /discovery/sources`):**

- All endpoints return 404 (`DiscoveryNotConfigured`) when no orchestrator is configured
- Approve/reject dispatch to `DiscoveryOrchestrator` async methods
- Sources status aggregated from `get_sources_status()`

### Task 2: Config and System Endpoints (TDD)

**Config (`GET /config/`, `POST /config/reload`):**

- `GET /config/` returns sanitized config from `config_repository` or minimal fallback
- Sensitive keys containing `secret`, `key`, `token`, `password` stripped from response
- `POST /config/reload` dispatches `ReloadConfigurationCommand` with optional `config_path` and `graceful` body params (defaults: `config_path=None`, `graceful=True`)

**System (`GET /system/`):**

- Dispatches `GetSystemMetricsQuery` for live metrics
- Augments with `uptime_seconds` (since module load) and `version` (from `mcp_hangar.__version__`)
- Returns combined payload under `{"system": {...}}`

## Commits

| Task | Phase | Commit | Description |
|------|-------|--------|-------------|
| 1 | RED | `27e94a0` | test(11-02): add failing tests for group and discovery API endpoints |
| 1 | GREEN | `384a63c` | feat(11-02): implement group and discovery REST API endpoints |
| 2 | RED | `e8a7113` | test(11-02): add failing tests for config and system API endpoints |
| 2 | GREEN | `babefd0` | feat(11-02): implement config and system REST API endpoints |

## Test Coverage

- `test_api_groups.py`: 16 tests — all passing
- `test_api_discovery.py`: 20 tests — all passing
- `test_api_config_system.py`: 22 tests — all passing
- Full suite: 2422 passed, 39 skipped, 0 failures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Removed spurious system.get_context patch from test fixture**

- **Found during:** Task 2 GREEN
- **Issue:** `test_api_config_system.py` fixture patched `mcp_hangar.server.api.system.get_context` but `system.py` intentionally does not import `get_context` (it uses only `dispatch_query`). This caused `AttributeError` at fixture setup time.
- **Fix:** Removed the inner `with patch("mcp_hangar.server.api.system.get_context", ...)` wrapper from the `api_client` fixture. The two remaining patches (middleware and config) are sufficient.
- **Files modified:** `packages/core/tests/unit/test_api_config_system.py`
- **Commit:** `babefd0`

## Decisions Made

1. **`ProviderGroup.to_status_dict()`** used for group serialization — provides a consistent, tested representation of group state including member details.
2. **`DiscoveryNotConfigured` extends `ProviderNotFoundError`** (not just `MCPError`) so the existing HTTP status map in middleware automatically maps it to 404 without any additional routing logic.
3. **`system.py` imports only `dispatch_query`** (no `get_context`) because the system endpoint needs only the query bus, not direct context access. This keeps the module's dependency surface minimal.
4. **Config sanitization is top-level only** (non-recursive) as a practical defense-in-depth measure — nested sensitive keys are an edge case not warranting recursive traversal complexity.

## Self-Check: PASSED

All created files confirmed present on disk. All four task commits (`27e94a0`, `384a63c`, `e8a7113`, `babefd0`) confirmed in git history. Full test suite: 2422 passed, 0 failures.
