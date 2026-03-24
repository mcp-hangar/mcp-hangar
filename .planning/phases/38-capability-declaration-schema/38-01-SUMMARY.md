---
phase: 38-capability-declaration-schema
plan: 01
subsystem: domain
tags: [value-objects, provider-config, yaml-deserialization, capabilities, ddd]

# Dependency graph
requires:
  - phase: 35-extract-enterprise-contracts
    provides: "Domain contracts and value object patterns for enterprise boundary"
provides:
  - "ProviderCapabilities.from_dict() factory for YAML deserialization"
  - "Provider aggregate accepts optional capabilities parameter"
  - "ProviderConfig dataclass includes capabilities field"
  - "server/config.py parses capabilities from YAML with error wrapping"
affects: [38-03-round-trip-tests, 39-networkpolicy-generation, 40-enforcement-loop, 41-admission-verification]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "from_dict() factory classmethod on frozen dataclass value objects"
    - "ValueError at domain boundary, ConfigurationError wrapping at server boundary"
    - "structlog warning for missing optional config blocks"

key-files:
  created:
    - tests/unit/test_capabilities.py (10 new tests added to existing file)
  modified:
    - src/mcp_hangar/domain/value_objects/capabilities.py
    - src/mcp_hangar/domain/model/provider.py
    - src/mcp_hangar/domain/model/provider_config.py
    - src/mcp_hangar/server/config.py

key-decisions:
  - "from_dict(None) and from_dict({}) both return default unconstrained ProviderCapabilities"
  - "ValueError stays in domain layer; ConfigurationError wrapping happens at server/config.py boundary only"
  - "Missing capabilities block logs structlog warning, not error -- backward compatible"
  - "capabilities parameter added after log_buffer in Provider.__init__ signature"

patterns-established:
  - "from_dict() factory: frozen dataclass VOs use classmethod for dict deserialization"
  - "Boundary error wrapping: domain raises ValueError, server/config.py catches and wraps in ConfigurationError"

requirements-completed: [CAP-VO, CAP-CONFIG]

# Metrics
duration: 4min
completed: 2026-03-24
---

# Phase 38 Plan 01: Capability Declaration Schema (Python Wiring) Summary

**ProviderCapabilities.from_dict() factory with full YAML deserialization chain: config.py -> ProviderCapabilities -> ProviderConfig -> Provider aggregate**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T18:22:34Z
- **Completed:** 2026-03-24T18:26:42Z
- **Tasks:** 2
- **Files modified:** 5 (4 source + 1 test)

## Accomplishments
- Added `from_dict()` classmethod to `ProviderCapabilities` for YAML config deserialization with full sub-object parsing (network, filesystem, environment, tools, resources)
- Wired `capabilities` parameter through entire Provider stack: `__init__`, property, `from_config()`, `to_config_dict()`
- Integrated capabilities into `ProviderConfig.from_dict()` and `server/config.py` with proper error boundary (ValueError -> ConfigurationError wrapping)
- 10 new tests added, all 35 tests pass (25 existing + 10 new)

## Task Commits

Each task was committed atomically:

1. **Task 1: Add from_dict() factory to ProviderCapabilities and wire into Provider aggregate (TDD)**
   - `d5109ee` (test) -- RED: 10 failing tests for from_dict and Provider capabilities
   - `752b02d` (feat) -- GREEN: Implementation passing all tests
2. **Task 2: Wire capabilities into ProviderConfig and server config loading** - `70f86c7` (feat)

## Files Created/Modified
- `src/mcp_hangar/domain/value_objects/capabilities.py` - Added `from typing import Any` import and `from_dict()` classmethod (~70 lines) for YAML dict deserialization
- `src/mcp_hangar/domain/model/provider.py` - Added `ProviderCapabilities` import, `capabilities` parameter to `__init__`, `self._capabilities` storage, `capabilities` property, wired through `from_config()` and `to_config_dict()`
- `src/mcp_hangar/domain/model/provider_config.py` - Added `ProviderCapabilities` import, `capabilities` field on dataclass, parsing in `from_dict()`
- `src/mcp_hangar/server/config.py` - Added `ProviderCapabilities` import, capabilities parsing with ConfigurationError wrapping, warning for missing capabilities
- `tests/unit/test_capabilities.py` - Added `TestProviderCapabilitiesFromDict` (7 tests) and `TestProviderCapabilitiesOnProvider` (3 tests)

## Decisions Made
- `from_dict(None)` and `from_dict({})` both return default unconstrained ProviderCapabilities -- treats missing/empty as "no constraints declared"
- ValueError stays in domain layer for purity; ConfigurationError wrapping happens only at server/config.py boundary per research recommendation
- Missing capabilities block logs structlog warning with hint to add capabilities, not an error -- backward compatible with existing configs
- `capabilities` parameter added after `log_buffer` in Provider.__init__ to maintain parameter ordering convention

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ProviderCapabilities value object fully wired through Python stack, ready for:
  - 38-03: Round-trip tests, config loading integration tests, example configs
  - 39: NetworkPolicy generation can read capabilities from Provider aggregate
  - 40-41: Enforcement loop and admission can check capabilities.enforcement_mode

---
*Phase: 38-capability-declaration-schema*
*Completed: 2026-03-24*
