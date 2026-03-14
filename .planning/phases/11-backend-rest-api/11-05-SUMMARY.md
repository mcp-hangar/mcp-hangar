---
phase: 11-backend-rest-api
plan: "05"
subsystem: server/api + application/event_handlers
tags: [rest-api, observability, metrics, audit-log, security-events, alerts, singleton, tdd, gap-closure]
dependency_graph:
  requires: [event-handlers-bootstrap, security-handler-singleton, prometheus-metrics]
  provides: [observability-rest-endpoints, audit-handler-singleton, alert-handler-singleton]
  affects: [server/api/router.py, application/event_handlers/__init__.py, server/bootstrap/event_handlers.py]
tech_stack:
  added: []
  patterns: [tdd-red-green-refactor, module-level-singleton, starlette-routes, defensive-attribute-access]
key_files:
  created:
    - packages/core/mcp_hangar/server/api/observability.py
    - packages/core/tests/unit/test_api_observability.py
  modified:
    - packages/core/mcp_hangar/application/event_handlers/audit_handler.py
    - packages/core/mcp_hangar/application/event_handlers/alert_handler.py
    - packages/core/mcp_hangar/application/event_handlers/__init__.py
    - packages/core/mcp_hangar/server/bootstrap/event_handlers.py
    - packages/core/mcp_hangar/server/api/router.py
decisions:
  - "SecurityEventHandler sink accessed defensively via getattr(_sink) then getattr(sink) -- private attribute name not guaranteed by public API"
  - "Alert level filtering performed in API layer not handler -- alerts_sent property returns full list, filtering is a query concern not a handler concern"
  - "Metrics JSON summary built by parsing Prometheus text lines with known prefixes -- avoids coupling to prometheus_client internals while providing actionable numbers"
metrics:
  duration: "25 minutes"
  completed: "2026-03-14T15:52:00Z"
  tasks_completed: 2
  files_created: 2
  files_modified: 5
---

# Phase 11 Plan 05: Observability REST Endpoints Summary

**One-liner:** Global handler singletons for audit/alert plus four read-only observability endpoints mounted at `/observability`.

## What Was Built

### Task 1: Global accessor singletons for audit and alert handlers

Added `get_audit_handler()` / `reset_audit_handler()` to `audit_handler.py` and `get_alert_handler()` / `reset_alert_handler()` to `alert_handler.py`, following the existing `get_security_handler()` pattern. Both functions follow the lazy-init singleton pattern with a module-level `_handler` variable.

Updated `event_handlers/__init__.py` to export all four new functions and updated `server/bootstrap/event_handlers.py` to subscribe the singleton instances instead of local variables — ensuring the same handler instances that receive events are accessible via the getters.

### Task 2: Observability REST endpoints (TDD)

Created `server/api/observability.py` with four read-only endpoints:

| Endpoint | Response |
|---|---|
| `GET /api/observability/metrics` | `{"prometheus_text": str, "summary": {"tool_calls_total": float, "health_checks_total": float}}` |
| `GET /api/observability/audit` | `{"records": [...], "total": int}` — supports `?provider_id=`, `?event_type=`, `?limit=` |
| `GET /api/observability/security` | `{"events": [...], "total": int}` — supports `?limit=` |
| `GET /api/observability/alerts` | `{"alerts": [...], "total": int}` — supports `?level=`, `?limit=` |

Mounted at `/observability` in `router.py`. All `limit` parameters are clamped to [1, 1000].

21 unit tests written first (TDD RED) then all passing after implementation (TDD GREEN).

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check

### Files Exist

- `packages/core/mcp_hangar/server/api/observability.py` — FOUND
- `packages/core/tests/unit/test_api_observability.py` — FOUND

### Commits

- `c6d71b3` feat(11-05): add get_audit_handler() and get_alert_handler() global singletons — FOUND
- `3f80811` test(11-05): add failing tests for observability REST endpoints — FOUND
- `ef7de8b` feat(11-05): create observability REST endpoints and mount in router — FOUND

## Self-Check: PASSED
