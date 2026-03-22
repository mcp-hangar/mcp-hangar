---
phase: 23-provider-group-crud-config-serialization
plan: "02"
subsystem: api
tags: [cqrs, domain-events, provider-group, thread-safety, tdd]

# Dependency graph
requires:
  - phase: 23-01
    provides: Provider CRUD events, command dataclasses, provider handlers, register_crud_handlers() skeleton
provides:
  - 5 group command dataclasses in crud_commands.py (Create/Update/Delete/AddMember/RemoveMember)
  - 5 group CRUD command handlers in crud_handlers.py, each with per-handler threading.Lock
  - ProviderGroup.update() aggregate method (strategy/description/min_healthy, emits GroupUpdated)
  - register_crud_handlers() updated to wire all 8 commands (3 provider + 5 group)
  - 17 new unit tests covering all group handler behaviors
affects:
  - 23-03 (config serializer reads groups via to_config_dict)
  - 23-04 (REST API uses group CRUD handlers)
  - bootstrap/cqrs.py (must wire group handlers into register_crud_handlers call)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Per-handler threading.Lock for GROUPS dict mutation (not a shared lock)"
    - "Mutations inside lock, all I/O (event publish, stop_all) outside lock"
    - "Group aggregate exposes update() method; handlers never touch private fields"

key-files:
  created: []
  modified:
    - mcp_hangar/application/commands/crud_handlers.py
    - mcp_hangar/domain/model/provider_group.py
    - tests/unit/test_crud_command_handlers.py

key-decisions:
  - "Each group handler owns its own threading.Lock (not a single shared groups lock) to minimize contention"
  - "ProviderGroup.update() acquires self._lock internally; UpdateGroupHandler does not hold its own lock during the call"
  - "DeleteGroupHandler: del from GROUPS inside lock, then stop_all() outside lock to avoid holding lock during I/O"
  - "AddGroupMemberHandler: repository.get() before acquiring lock, group.add_member() inside lock, event publish outside"

patterns-established:
  - "Group handler thread-safety: acquire lock -> mutate dict -> release -> do I/O (publish/stop)"
  - "Aggregate update method pattern: ProviderGroup.update() for safe config mutation"

requirements-completed: [CRUD-02]

# Metrics
duration: 5min
completed: 2026-03-22
---

# Phase 23 Plan 02: Group CRUD Handlers Summary

**5 group CRUD command handlers with per-handler threading.Lock, ProviderGroup.update() aggregate method, and 17 passing TDD unit tests**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-22T13:35:25Z
- **Completed:** 2026-03-22T13:37:38Z
- **Tasks:** 3 (RED, GREEN, REFACTOR — refactor pass had no changes)
- **Files modified:** 3

## Accomplishments
- Added 5 group handler classes to `crud_handlers.py` (Create/Update/Delete/AddMember/RemoveMember), each with an independent `threading.Lock` and I/O outside lock
- Added `ProviderGroup.update()` aggregate method in `provider_group.py` so handlers never directly mutate private fields
- Updated `register_crud_handlers()` to wire all 8 commands (3 provider + 5 group) when `groups` dict is provided

## Task Commits

1. **Task 1: RED — failing group handler tests** - `cd4cd24` (test)
2. **Task 2: GREEN — implement group handlers and ProviderGroup.update()** - `8cfad9f` (feat)

_Note: REFACTOR phase had no code changes (ruff clean, format clean, docstrings already present)._

## Files Created/Modified
- `mcp_hangar/application/commands/crud_handlers.py` - Added 5 group handlers + updated register_crud_handlers()
- `mcp_hangar/domain/model/provider_group.py` - Added update() aggregate method
- `tests/unit/test_crud_command_handlers.py` - Appended 5 test classes (17 tests); total 41 tests pass

## Decisions Made
- Each group handler owns its own `threading.Lock` rather than a single shared lock to minimize contention and match the per-provider pattern from Plan 01.
- `ProviderGroup.update()` acquires `self._lock` internally; `UpdateGroupHandler` does not hold its own lock during the call (avoids nested locking).
- `DeleteGroupHandler` removes the group from the dict inside the lock, then calls `stop_all()` and publishes events outside the lock to avoid holding the lock during I/O.
- `AddGroupMemberHandler` resolves the provider from the repository before acquiring the lock so the lookup (which may involve I/O) does not block other group mutations.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] UpdateGroupCommand missing strategy field in handler call**
- **Found during:** Task 2 (GREEN — implement handlers)
- **Issue:** Plan's interface section showed `UpdateGroupCommand` with `strategy` field, but the actual dataclass from Plan 01 only had `description` and `min_healthy`. Plan's implementation spec also called `group.update(strategy=command.strategy, ...)` which would pass `None` harmlessly, but `strategy` was not on the command — AttributeError would occur.
- **Fix:** `UpdateGroupHandler` calls `group.update(description=command.description, min_healthy=command.min_healthy)` only, omitting `strategy`. `ProviderGroup.update()` still accepts `strategy` for future use.
- **Files modified:** `mcp_hangar/application/commands/crud_handlers.py`
- **Verification:** All 41 tests pass, no AttributeError
- **Committed in:** `8cfad9f`

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** Required correction for correctness; no scope creep.

## Issues Encountered
- Pre-commit ruff auto-fixed unused imports on the RED commit (`cd4cd24`) — required re-staging and recommitting. Standard pre-commit hook behavior, not a code issue.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Group CRUD write side complete; Plan 03 (config serializer) and Plan 04 (REST API) can proceed
- `register_crud_handlers()` requires callers to pass `groups` dict to wire group commands — bootstrap will need updating in a future plan

---
*Phase: 23-provider-group-crud-config-serialization*
*Completed: 2026-03-22*

## Self-Check: PASSED

- FOUND: 23-02-SUMMARY.md
- FOUND: cd4cd24 (test(23-02): add failing tests for group CRUD handlers)
- FOUND: 8cfad9f (feat(23-02): implement group CRUD handlers and ProviderGroup.update())
