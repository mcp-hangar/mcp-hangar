---
phase: 38-capability-declaration-schema
plan: 02
subsystem: operator
tags: [kubernetes, crd, kubebuilder, go, capabilities, networkpolicy]

# Dependency graph
requires:
  - phase: 38-capability-declaration-schema
    provides: "Python ProviderCapabilities value object (plan 01)"
provides:
  - "ProviderCapabilities Go struct in MCPProvider CRD (spec + status)"
  - "CRD YAML manifests with capabilities field"
  - "Reconciler propagation of spec.capabilities to status.capabilities"
affects: [39-networkpolicy-generation, 40-operator-enforcement-loop]

# Tech tracking
tech-stack:
  added: [controller-tools v0.17.2]
  patterns: ["CRD spec-to-status mirroring via DeepCopy", "Kubebuilder validation markers for nested structs"]

key-files:
  created:
    - "packages/operator/config/crd/bases/mcp-hangar.io_mcpproviders.yaml"
    - "packages/operator/config/crd/bases/mcp-hangar.io_mcpprovidergroups.yaml"
    - "packages/operator/config/crd/bases/mcp-hangar.io_mcpdiscoverysources.yaml"
  modified:
    - "packages/operator/api/v1alpha1/mcpprovider_types.go"
    - "packages/operator/api/v1alpha1/zz_generated.deepcopy.go"
    - "packages/operator/internal/controller/mcpprovider_controller.go"
    - "packages/operator/Makefile"

key-decisions:
  - "Used Spec suffix on sub-structs (NetworkCapabilitiesSpec, EgressRuleSpec) to avoid collision with existing Capabilities struct in SecurityContext"
  - "Bumped controller-tools from v0.14.0 to v0.17.2 for Go 1.26 compatibility"
  - "Added capabilities propagation in both container and remote reconciliation paths"

patterns-established:
  - "CRD spec-to-status mirroring: use DeepCopy to avoid shared pointers between spec and status"

requirements-completed: [CAP-CRD]

# Metrics
duration: 4min
completed: 2026-03-24
---

# Phase 38 Plan 02: CRD Capability Types Summary

**ProviderCapabilities Go structs in MCPProvider CRD with kubebuilder validation markers and reconciler spec-to-status propagation**

## Performance

- **Duration:** 4 min
- **Started:** 2026-03-24T18:22:33Z
- **Completed:** 2026-03-24T18:27:00Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Added complete ProviderCapabilities type hierarchy (7 Go structs) with kubebuilder validation markers
- Added Capabilities field to both MCPProviderSpec and MCPProviderStatus
- Reconciler propagates spec.capabilities to status.capabilities via DeepCopy in both container and remote paths
- Generated CRD YAML manifests include capabilities with full OpenAPI validation schema

## Task Commits

Each task was committed atomically:

1. **Task 1: Add ProviderCapabilities Go structs to CRD types** - `9149eb5` (feat)
2. **Task 2: Update reconciler to store capabilities in status** - `d68093f` (feat)

## Files Created/Modified
- `packages/operator/api/v1alpha1/mcpprovider_types.go` - ProviderCapabilities, NetworkCapabilitiesSpec, EgressRuleSpec, FilesystemCapabilitiesSpec, EnvironmentCapabilitiesSpec, ToolCapabilitiesSpec, ResourceCapabilitiesSpec structs
- `packages/operator/api/v1alpha1/zz_generated.deepcopy.go` - Regenerated DeepCopy methods for all new types
- `packages/operator/config/crd/bases/mcp-hangar.io_mcpproviders.yaml` - CRD YAML with capabilities validation schema
- `packages/operator/config/crd/bases/mcp-hangar.io_mcpprovidergroups.yaml` - Regenerated CRD manifest
- `packages/operator/config/crd/bases/mcp-hangar.io_mcpdiscoverysources.yaml` - Regenerated CRD manifest
- `packages/operator/internal/controller/mcpprovider_controller.go` - Capabilities propagation in syncPodStatus and reconcileRemoteProvider
- `packages/operator/Makefile` - Bumped controller-tools version to v0.17.2

## Decisions Made
- Used `Spec` suffix on sub-struct names (e.g., `NetworkCapabilitiesSpec`, `EgressRuleSpec`) to avoid name collision with existing `Capabilities` struct in `SecurityContext`
- Bumped controller-tools from v0.14.0 to v0.17.2 because v0.14.0 was incompatible with Go 1.26 (golang.org/x/tools issue)
- Added capabilities propagation in both syncPodStatus (container mode) and reconcileRemoteProvider (remote mode) to cover all provider types

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Bumped controller-tools from v0.14.0 to v0.17.2**
- **Found during:** Task 1 (make generate)
- **Issue:** controller-gen v0.14.0 failed to build with Go 1.26.1 due to golang.org/x/tools tokeninternal incompatibility
- **Fix:** Updated CONTROLLER_TOOLS_VERSION in Makefile from v0.14.0 to v0.17.2
- **Files modified:** packages/operator/Makefile
- **Verification:** make generate, make manifests, go vet, go build all pass
- **Committed in:** 9149eb5 (Task 1 commit)

**2. [Rule 2 - Missing Critical] Added capabilities propagation to remote provider path**
- **Found during:** Task 2 (reconciler update)
- **Issue:** Plan only specified adding capabilities propagation generically, but the reconciler has separate paths for container (syncPodStatus) and remote (reconcileRemoteProvider) providers. Missing either path would leave capabilities unpropagated for that provider type.
- **Fix:** Added capabilities propagation in both syncPodStatus and reconcileRemoteProvider
- **Files modified:** packages/operator/internal/controller/mcpprovider_controller.go
- **Verification:** go build passes
- **Committed in:** d68093f (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (1 blocking, 1 missing critical)
**Impact on plan:** Both fixes essential for correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- CRD capabilities field is ready for Phase 39 (NetworkPolicy generation) to consume from status.capabilities
- Reconciler pattern established for spec-to-status mirroring with DeepCopy

## Self-Check: PASSED

All key files verified on disk, all commits verified in git log.

---
*Phase: 38-capability-declaration-schema*
*Completed: 2026-03-24*
