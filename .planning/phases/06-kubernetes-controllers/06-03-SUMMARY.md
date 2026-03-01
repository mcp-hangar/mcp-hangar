---
phase: 06-kubernetes-controllers
plan: "03"
subsystem: operator/internal/controller, operator/cmd
tags: [envtest, integration-tests, controller-wiring, kubernetes]
dependency_graph:
  requires: [06-01, 06-02]
  provides: [envtest-suite, integration-tests, main-wiring]
  affects: [packages/operator]
tech_stack:
  added: [envtest, setup-envtest]
  patterns: [TestMain-based-suite, require.Eventually-polling, namespace-isolation]
key_files:
  created:
    - packages/operator/internal/controller/suite_test.go
    - packages/operator/internal/controller/mcpprovidergroup_controller_test.go
    - packages/operator/internal/controller/mcpdiscoverysource_controller_test.go
  modified:
    - packages/operator/cmd/operator/main.go
decisions:
  - TestMain-based envtest over Ginkgo/Gomega per project testify convention
  - metricsserver.Options{BindAddress:"0"} to disable metrics port in tests
  - Each test creates own namespace for complete isolation
  - require.Eventually with retry for annotation updates to handle controller conflict errors
metrics:
  completed: "2026-03-01"
  tasks: 2
  files_created: 3
  files_modified: 1
requirements: [K8S-07]
---

# Phase 6 Plan 3: envtest Integration Tests and Controller Wiring Summary

envtest suite with 12 integration tests covering MCPProviderGroup (label selection, status aggregation, health policy, zero-member, coexisting conditions, deletion) and MCPDiscoverySource (ConfigMap discovery, additive sync, authoritative deletion, owner references, paused freeze, deletion cleanup), plus main.go wiring for all 3 controllers.

## What Was Done

### Task 1: envtest suite and MCPProviderGroup integration tests

Created `suite_test.go` with TestMain-based envtest setup that loads all 3 CRDs (MCPProvider, MCPProviderGroup, MCPDiscoverySource) from helm-charts/mcp-hangar-operator/crds/ and registers both MCPProviderGroupReconciler and MCPDiscoverySourceReconciler with the envtest manager. Metrics disabled via `metricsserver.Options{BindAddress: "0"}` to avoid port conflicts.

Created `mcpprovidergroup_controller_test.go` with 6 integration tests:

1. **LabelSelection** -- verifies selector picks matching providers and ignores non-matching
2. **StatusAggregation** -- verifies all 5 state counts (Ready=2, Degraded=1, Dead=1, Cold=1)
3. **HealthPolicyThreshold** -- verifies Ready=True at exactly 60% threshold
4. **ZeroMembers** -- verifies Ready=Unknown with reason NoProviders, Available=False
5. **CoexistingReadyDegraded** -- verifies Ready=True AND Degraded=True simultaneously above threshold
6. **Deletion** -- verifies finalizer cleanup and providers preserved after group deletion

### Task 2: MCPDiscoverySource integration tests and controller wiring

Created `mcpdiscoverysource_controller_test.go` with 6 integration tests:

1. **ConfigMapDiscovery** -- verifies 2 MCPProvider CRs created from ConfigMap YAML with correct labels and owner refs
2. **AdditiveNeverDeletes** -- verifies providers preserved when ConfigMap shrinks in additive mode
3. **AuthoritativeDeletes** -- verifies stale providers deleted when ConfigMap shrinks in authoritative mode
4. **OwnerReferences** -- verifies owner ref UID/Kind/Name and cascade deletion on source removal
5. **PausedFreeze** -- verifies no new providers created while paused, resumes after unpause
6. **Deletion** -- verifies finalizer cleanup removes managed providers

Wired MCPProviderGroupReconciler and MCPDiscoverySourceReconciler into `main.go` after existing MCPProviderReconciler, following identical registration pattern (Client, Scheme, Recorder, HangarClient).

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed conflict errors in annotation-triggered reconcile tests**

- **Found during:** Task 2
- **Issue:** `TestMCPDiscoverySource_AdditiveNeverDeletes` and `TestMCPDiscoverySource_AuthoritativeDeletes` failed because annotating the MCPDiscoverySource to trigger a reconcile raced with the controller's own status updates, causing "the object has been modified" conflict errors
- **Fix:** Changed both tests to use `require.Eventually` with retry pattern: re-fetch source, apply annotation, attempt update, retry on conflict
- **Files modified:** `packages/operator/internal/controller/mcpdiscoverysource_controller_test.go`
- **Commits:** 1fcd284

## Verification

- All 14 tests pass: `go test ./internal/controller/ -v -count=1 -timeout 120s` (2 unit + 6 group + 6 discovery)
- Binary compiles: `go build ./cmd/operator/` succeeds
- All 3 CRDs load correctly in envtest
- Each test uses unique namespace for isolation
- No Ginkgo/Gomega usage -- testify only

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 285e9d7 | test(06-03): add envtest suite and MCPProviderGroup integration tests |
| 2 | 1fcd284 | test(06-03): add MCPDiscoverySource integration tests and wire controllers into main.go |

## Self-Check: PASSED

All 5 files found. Both commits verified.
