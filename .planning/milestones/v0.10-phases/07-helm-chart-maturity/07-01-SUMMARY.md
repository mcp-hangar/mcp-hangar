---
phase: 07-helm-chart-maturity
plan: 01
subsystem: infra
tags: [helm, kubernetes, chart-testing, versioning]

# Dependency graph
requires:
  - phase: 06-kubernetes-controllers
    provides: Helm chart templates for operator and server
provides:
  - Both charts versioned at 0.10.0
  - Post-install NOTES.txt with usage instructions for both charts
  - Helm test templates for installation validation
affects: []

# Tech tracking
tech-stack:
  added: []
  patterns: [helm-test-hook-pattern, notes-txt-static-content]

key-files:
  created:
    - packages/helm-charts/mcp-hangar/templates/NOTES.txt
    - packages/helm-charts/mcp-hangar/templates/tests/test-connection.yaml
    - packages/helm-charts/mcp-hangar-operator/templates/NOTES.txt
    - packages/helm-charts/mcp-hangar-operator/templates/tests/test-health.yaml
  modified:
    - packages/helm-charts/mcp-hangar/Chart.yaml
    - packages/helm-charts/mcp-hangar-operator/Chart.yaml
    - .pre-commit-config.yaml

key-decisions:
  - "Static NOTES.txt content with only structural template refs (no Go conditionals)"
  - "busybox:1.37 pinned image for Helm test pods with wget --spider pattern"
  - "hook-delete-policy: before-hook-creation,hook-succeeded for clean test reruns (Helm v4 compatible)"

patterns-established:
  - "Helm test pattern: busybox wget --spider to service endpoint with timeout"
  - "NOTES.txt pattern: static text with Release/Values template refs only"

requirements-completed: [HELM-01, HELM-02, HELM-03]

# Metrics
duration: 2min
completed: 2026-03-01
---

# Phase 7 Plan 1: Helm Chart Maturity Summary

**Version-synchronized both Helm charts to 0.10.0 with NOTES.txt post-install guidance and Helm test templates for installation validation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-01T17:22:01Z
- **Completed:** 2026-03-01T17:24:10Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments

- Both Chart.yaml files bumped to version 0.10.0 / appVersion 0.10.0
- Server NOTES.txt with port-forward, health check, logs, and documentation links
- Operator NOTES.txt with CRD status commands, CRD removal warning, and documentation links
- Server test template validates /health endpoint through service
- Operator test template validates metrics service endpoint
- Server Chart.yaml gained kubeVersion constraint for parity with operator

## Task Commits

Each task was committed atomically:

1. **Task 1: Version bump and NOTES.txt for both charts** - `10644f6` (feat)
2. **Task 2: Helm test templates for both charts** - `9be2fca` (feat)

## Files Created/Modified

- `packages/helm-charts/mcp-hangar/Chart.yaml` - Version bump to 0.10.0, added kubeVersion constraint
- `packages/helm-charts/mcp-hangar/templates/NOTES.txt` - Post-install instructions with port-forward, health, logs, docs
- `packages/helm-charts/mcp-hangar/templates/tests/test-connection.yaml` - Helm test pod targeting /health endpoint
- `packages/helm-charts/mcp-hangar-operator/Chart.yaml` - Version bump to 0.10.0
- `packages/helm-charts/mcp-hangar-operator/templates/NOTES.txt` - Post-install instructions with CRD commands, removal warning, docs
- `packages/helm-charts/mcp-hangar-operator/templates/tests/test-health.yaml` - Helm test pod targeting metrics service
- `.pre-commit-config.yaml` - Extended check-yaml exclude pattern for packages/helm-charts path

## Decisions Made

- Static NOTES.txt content per user decision (no Go template conditionals, only structural refs)
- busybox:1.37 pinned for lightweight test pods, wget --spider for URL check without download
- hook-delete-policy includes both before-hook-creation and hook-succeeded for Helm v4 compatibility

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed pre-commit check-yaml exclude pattern for Helm templates**

- **Found during:** Task 2 (Helm test templates)
- **Issue:** Pre-commit check-yaml hook excluded `deploy/helm/` but charts live at `packages/helm-charts/`, causing commit rejection on Go template YAML
- **Fix:** Extended exclude regex to `'^(deploy/helm|packages/helm-charts)/.*/templates/.*\.yaml$'`
- **Files modified:** .pre-commit-config.yaml
- **Verification:** Pre-commit passes, helm lint still validates templates correctly
- **Committed in:** 9be2fca (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Essential fix for committing Helm template files. No scope creep.

## Issues Encountered

None

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 7 Plan 1 complete -- this is the only plan for Phase 7
- v0.10 milestone complete: all 7 phases executed
- Both Helm charts are production-ready at version 0.10.0

## Self-Check: PASSED

All 7 created/modified files verified on disk. Both task commits (10644f6, 9be2fca) verified in git history.

---
*Phase: 07-helm-chart-maturity*
*Completed: 2026-03-01*
