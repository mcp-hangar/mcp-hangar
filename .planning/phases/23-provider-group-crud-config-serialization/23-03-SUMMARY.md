---
phase: 23-provider-group-crud-config-serialization
plan: "03"
subsystem: api
tags: [yaml, serialization, provider-group, config, backup, tdd]

# Dependency graph
requires:
  - phase: 23-provider-group-crud-config-serialization
    provides: "Plan 01 - Provider.to_config_dict() already implemented"
provides:
  - "ProviderGroup.to_config_dict() returning YAML-compatible config dict"
  - "server/config_serializer.py with serialize_providers, serialize_groups, serialize_full_config, write_config_backup"
  - "bak1..bak5 rotation logic for config backup files"
affects:
  - 23-provider-group-crud-config-serialization/23-04 (REST endpoints wiring)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Optional explicit dicts for testability without patching (serialize_providers(providers=...) pattern)"
    - "bak1..bak5 rotation via pathlib.rename()"
    - "yaml.safe_dump only - never yaml.dump - prevents Python object tags in YAML output"

key-files:
  created:
    - mcp_hangar/server/config_serializer.py
    - tests/unit/test_config_serializer.py
  modified:
    - mcp_hangar/domain/model/provider_group.py

key-decisions:
  - "serialize_full_config() receives optional providers/groups args so tests don't need to patch get_context() - testability without patching"
  - "to_config_dict() omits description key entirely when None (not present), matches config.py load behavior"
  - "yaml.safe_dump with sort_keys=True, allow_unicode=True for deterministic YAML output"

patterns-established:
  - "Config serializer pattern: serialize_providers(providers=None) - None triggers context fetch, explicit dict bypasses it"
  - "Backup rotation pattern: range(5, 1, -1) for bak4->bak5...bak1->bak2, then write new bak1"

requirements-completed:
  - CRUD-03

# Metrics
duration: 3min
completed: 2026-03-22
---

# Phase 23 Plan 03: Config Serializer Summary

**ProviderGroup.to_config_dict() and config_serializer module with bak1..bak5 rotation, inverse of server/config.py**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-22T12:22:30Z
- **Completed:** 2026-03-22T12:25:43Z
- **Tasks:** 3 (RED, GREEN, REFACTOR)
- **Files modified:** 3

## Accomplishments
- Added `to_config_dict()` to `ProviderGroup` aggregate returning YAML-compatible dict with mode, strategy, min_healthy, auto_start, members, and optional description
- Created `server/config_serializer.py` with all four public functions: `serialize_providers()`, `serialize_groups()`, `serialize_full_config()`, `write_config_backup()`
- 24 unit tests across 5 test classes, all passing with zero linting/typing errors

## Task Commits

Each task was committed atomically:

1. **RED: Failing tests for config serializer** - `db7d49c` (test)
2. **GREEN: Implement ProviderGroup.to_config_dict() and config_serializer** - `91fa704` (feat)

_Note: No REFACTOR commit needed - implementation was clean on first pass_

## Files Created/Modified
- `mcp_hangar/domain/model/provider_group.py` - Added `to_config_dict()` method before existing `to_status_dict()`
- `mcp_hangar/server/config_serializer.py` - New module, inverse of server/config.py
- `tests/unit/test_config_serializer.py` - 24 unit tests across 5 test classes

## Decisions Made
- `serialize_full_config(providers=None, groups=None)` accepts optional explicit dicts so tests don't need to patch `get_context()` - enables clean unit testing without complex mocking
- `to_config_dict()` omits `description` key entirely when `None` (not just setting it to `None`), matching how `server/config.py`'s `_load_group_config` parses groups
- Used `yaml.safe_dump` throughout (never `yaml.dump`) to avoid Python object tags like `!!python/object` in output

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Config serializer module is ready for Plan 04 REST endpoint wiring
- `serialize_full_config()` and `write_config_backup()` are production-ready
- All verification criteria from plan met: tests pass, ruff clean, mypy clean

## Self-Check: PASSED

All key files exist on disk and all task commits verified in git log.

---
*Phase: 23-provider-group-crud-config-serialization*
*Completed: 2026-03-22*
