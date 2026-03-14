---
phase: 11-backend-rest-api
plan: "01"
subsystem: server/api
tags: [rest-api, starlette, cqrs, cors, tdd]
dependency_graph:
  requires: []
  provides: [api-foundation, provider-endpoints, /api/-mount]
  affects: [fastmcp_server/asgi.py, fastmcp_server/factory.py]
tech_stack:
  added: [starlette CORSMiddleware, HangarJSONEncoder, HangarJSONResponse]
  patterns: [CQRS dispatch via run_in_threadpool, domain-error-to-HTTP-status mapping, path-prefix stripping in ASGI router]
key_files:
  created:
    - packages/core/mcp_hangar/server/api/__init__.py
    - packages/core/mcp_hangar/server/api/middleware.py
    - packages/core/mcp_hangar/server/api/serializers.py
    - packages/core/mcp_hangar/server/api/router.py
    - packages/core/mcp_hangar/server/api/providers.py
    - packages/core/tests/unit/test_api_foundation.py
    - packages/core/tests/unit/test_api_providers.py
  modified:
    - packages/core/mcp_hangar/fastmcp_server/asgi.py
    - packages/core/mcp_hangar/fastmcp_server/factory.py
decisions:
  - "Starlette Mount('/providers', routes=provider_routes) used for clean prefix separation inside api_app"
  - "Path prefix /api stripped manually in combined_app before forwarding scope to api_app"
  - "CORS origins read from MCP_CORS_ORIGINS env var, defaulting to localhost:5173 for dev"
  - "Error envelope format: {error: {code, message, details}} — consistent across all 4xx/5xx responses"
  - "dispatch_query/dispatch_command use run_in_threadpool to keep ASGI non-blocking while query/command buses are synchronous"
  - "create_auth_combined_app routes /api/ to api_app without applying auth middleware — API endpoints handle auth separately"
metrics:
  duration: "multi-session (split execution)"
  completed: "2026-03-14"
  tasks_completed: 2
  tests_added: 59
  files_created: 7
  files_modified: 2
---

# Phase 11 Plan 01: API Foundation and Provider Endpoints Summary

REST API module foundation using pure Starlette with CQRS dispatch, domain-error-to-HTTP mapping, custom JSON serialization, CORS middleware, and all six provider endpoints mounted at `/api/providers/`.

## What Was Built

### Task 1 — API Foundation Module (TDD)

**RED:** 31 failing tests covering error handler mapping, error envelope format, CQRS dispatch helpers, JSON serializers, and CORS config.

**GREEN:** Implemented the full `mcp_hangar/server/api/` package:

- **`middleware.py`** — `error_handler` maps domain exceptions to HTTP status codes (404, 409, 422, 429, 401, 403, 503, 504, 500), `dispatch_query` / `dispatch_command` wrap synchronous buses with `run_in_threadpool`, `get_cors_config` reads `MCP_CORS_ORIGINS` env var.
- **`serializers.py`** — `HangarJSONEncoder` handles datetime, Enum, set, and `.to_dict()` objects; `HangarJSONResponse` uses it; `serialize_provider_summary`, `serialize_provider_details`, `serialize_tool_info`, `serialize_health_info` convert read models to dicts.
- **`router.py`** — `create_api_router()` builds a Starlette app with CORSMiddleware and exception handlers.
- **`providers.py`** — Six endpoint handlers (`list_providers`, `get_provider`, `start_provider`, `stop_provider`, `get_provider_tools`, `get_provider_health`) plus `provider_routes` list.
- **`__init__.py`** — Exports `create_api_router`.

### Task 2 — Provider Endpoints and ASGI Mount (TDD)

**RED / GREEN combined:** 28 tests (provider endpoint unit tests + ASGI integration) all passed after Task 1 implementation was already sufficient for the endpoint logic. ASGI integration tests confirmed routing worked after `asgi.py` and `factory.py` updates.

**ASGI updates:**

- `create_combined_asgi_app(aux_app, mcp_app, api_app=None)` — routes `/api/` paths to `api_app` with prefix stripped, falls through to `mcp_app` otherwise.
- `create_auth_combined_app(...)` — same `/api/` routing added before the auth gate.
- `MCPServerFactory.create_asgi_app()` — creates `api_app = create_api_router()` and passes it to the combined app factory.

## Error Mapping Table

| Domain Exception | HTTP Status |
|---|---|
| ProviderNotFoundError | 404 |
| ToolNotFoundError | 404 |
| ProviderNotReadyError | 409 |
| ValidationError | 422 |
| RateLimitExceeded | 429 |
| AuthenticationError | 401 |
| AccessDeniedError | 403 |
| ProviderDegradedError | 503 |
| ToolTimeoutError | 504 |
| MCPError (generic) | 500 |
| Exception (unhandled) | 500 (no internals leaked) |

## Deviations from Plan

None - plan executed exactly as written.

## Commits

| Hash | Message |
|---|---|
| `8365fd6` | test(11-01): add failing tests for API foundation (middleware, serializers, CORS) |
| `f0d2ecf` | feat(11-01): implement API foundation module (middleware, serializers, router, CORS) |
| `2dbe147` | feat(11-01): add provider endpoints and mount REST API at /api/ prefix |

## Self-Check: PASSED
