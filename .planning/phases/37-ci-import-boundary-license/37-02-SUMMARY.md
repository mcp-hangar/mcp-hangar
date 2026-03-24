---
phase: 37-ci-import-boundary-license
plan: 02
subsystem: docs
tags: [licensing, dual-license, mit, bsl, cla, contributing]

# Dependency graph
requires:
  - phase: 36-enterprise-directory-migration
    provides: enterprise/ directory with LICENSE.BSL and CLA.md
provides:
  - Dual-license disclosure in README.md
  - CLA requirement in CONTRIBUTING.md (root and docs/development/)
  - Updated pyproject.toml classifiers with topic metadata
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns: [dual-license documentation pattern]

key-files:
  created: []
  modified:
    - README.md
    - CONTRIBUTING.md
    - docs/development/CONTRIBUTING.md
    - pyproject.toml

key-decisions:
  - "Keep pyproject.toml license field as MIT since PyPI package only ships core (src/mcp_hangar/)"
  - "Added Topic classifiers for PyPI discoverability rather than adding a second license classifier"

patterns-established:
  - "Dual-license documentation: core directories listed as MIT, enterprise/ as BSL 1.1 with CLA"

requirements-completed: []

# Metrics
duration: 2min
completed: 2026-03-24
---

# Phase 37 Plan 02: License Documentation Summary

**Dual-license model (MIT core + BSL 1.1 enterprise) documented in README, CONTRIBUTING, and pyproject.toml with CLA instructions**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-24T17:45:17Z
- **Completed:** 2026-03-24T17:46:55Z
- **Tasks:** 2
- **Files modified:** 4

## Accomplishments
- README.md License section now communicates the dual-license model with links to both LICENSE and enterprise/LICENSE.BSL
- Both CONTRIBUTING.md files (root redirect and full docs/development/) include Licensing sections with CLA requirements for enterprise/ contributions
- pyproject.toml classifiers updated with Topic metadata for better PyPI discoverability

## Task Commits

Each task was committed atomically:

1. **Task 1: Update README.md license section and pyproject.toml classifiers** - `6ef159c` (docs)
2. **Task 2: Add CLA and licensing guidance to CONTRIBUTING.md files** - `bcbffcb` (docs)

## Files Created/Modified
- `README.md` - License section updated from "MIT" to dual-license disclosure with BSL 1.1 mention
- `CONTRIBUTING.md` - Added Licensing section between Quick Start and Code of Conduct
- `docs/development/CONTRIBUTING.md` - Replaced "## License: MIT" with full licensing table and CLA instructions
- `pyproject.toml` - Added Topic :: Software Development :: Libraries and Topic :: System :: Systems Administration classifiers

## Decisions Made
- Kept `license = {text = "MIT"}` in pyproject.toml since the PyPI distribution only ships core (src/mcp_hangar/) which is MIT. The enterprise/ directory is excluded from hatch build.
- Added Topic classifiers for discoverability rather than a second license classifier, since PyPI's classifier system does not cleanly support dual-licensing.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- License documentation track complete
- All contributor-facing docs now reflect dual-license model
- Ready for CI import boundary enforcement (Plan 01) or next phase

## Self-Check: PASSED

All modified files verified on disk. All commit hashes found in git log.

---
*Phase: 37-ci-import-boundary-license*
*Completed: 2026-03-24*
