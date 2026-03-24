---
phase: 40-enforcement-loop-violations
plan: 02
subsystem: operator
tags: [crd, metrics, prometheus, violations, enforcement, go]
dependency_graph:
  requires: []
  provides: [ViolationRecord-CRD, CapabilityViolationsTotal-metric, reconcileViolationDetection]
  affects: [packages/operator]
tech_stack:
  added: []
  patterns: [violation-detection, ring-buffer-capping, condition-toggle]
key_files:
  created:
    - packages/operator/internal/controller/violations_test.go
  modified:
    - packages/operator/api/v1alpha1/mcpprovider_types.go
    - packages/operator/api/v1alpha1/zz_generated.deepcopy.go
    - packages/operator/pkg/metrics/metrics.go
    - packages/operator/pkg/metrics/metrics_test.go
    - packages/operator/internal/controller/mcpprovider_controller.go
decisions:
  - ViolationRecord placed after Condition struct in CRD types for logical grouping
  - Ring-buffer capping drops oldest violations when exceeding MaxViolationRecords (100)
  - reconcileViolationDetection is non-blocking (same pattern as reconcileNetworkPolicy)
metrics:
  duration: 6m31s
  completed: 2026-03-24
  tasks_completed: 2
  tasks_total: 2
  test_count: 12
  files_changed: 6
---

# Phase 40 Plan 02: Operator Violation Detection Summary

ViolationRecord CRD status field, CapabilityViolationsTotal Prometheus counter, and reconcileViolationDetection reconcile step added to the Go operator for structured capability violation signaling.

## One-liner

ViolationRecord CRD type with ring-buffer capping, CapabilityViolationsTotal Prometheus counter, and reconcileViolationDetection detecting NetworkPolicy drift and tool count drift.

## What Was Built

### Task 1: ViolationRecord CRD type + CapabilityViolationsTotal metric

- **ViolationRecord struct** added to `mcpprovider_types.go` with Type, Detail, Severity, Action, Destination, Timestamp fields and kubebuilder validation markers (Enum constraints on Type, Severity, and Action).
- **MaxViolationRecords constant** (100) prevents CRD status size explosion against etcd 1.5MB limit.
- **Violations []ViolationRecord** field added to MCPProviderStatus, visible via kubectl.
- **CapabilityViolationsTotal** Prometheus counter registered with labels `[namespace, name, violation_type]`.
- **RecordViolation** convenience function for clean metric increment calls.
- DeepCopy regenerated via `make generate`.
- 2 new metric tests + 1 updated registration test.

### Task 2: reconcileViolationDetection + ViolationDetected condition

- **ConditionViolationDetected** constant and **ReasonViolationDetected**/**ReasonViolationCleared** event reasons added.
- **reconcileViolationDetection** method implements two detection rules:
  1. **NetworkPolicy drift**: capabilities declare network egress but `NetworkPolicyApplied` condition is not True.
  2. **Tool count drift**: `status.toolsCount` exceeds `spec.capabilities.tools.maxCount`.
- Called from `reconcileContainerProvider` after `reconcileNetworkPolicy`, following the same non-blocking error handling pattern.
- Violations capped at `MaxViolationRecords` with ring-buffer behavior (drops oldest).
- K8s Events emitted: Warning for violations, Normal for cleared.
- ViolationDetected condition set to True when violations exist, cleared to False when resolved.
- EnforcementMode defaults to "alert" when empty.
- 10 comprehensive unit tests covering all detection scenarios.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | `fe979ca` | ViolationRecord CRD type + CapabilityViolationsTotal metric |
| 2 | `eac7723` | reconcileViolationDetection + ViolationDetected condition + tests |

## Verification Results

- All Go tests pass: `go test ./... -v -count=1` (5 packages, 0 failures)
- Build succeeds: `go build ./...`
- DeepCopy regenerated: `make generate`
- No lint issues: `go vet ./...`

## Deviations from Plan

None -- plan executed exactly as written.

## Test Coverage

| Test | What It Verifies |
|------|-----------------|
| TestCapabilityViolationsTotal | Counter increments with correct labels |
| TestRecordViolation | Convenience function increments correct counter |
| TestMetricsRegistered | CapabilityViolationsTotal in registration list |
| TestReconcileViolationDetection_NilCapabilities | No-op when capabilities nil |
| TestReconcileViolationDetection_NoViolations | No violations when compliant |
| TestReconcileViolationDetection_NetworkPolicyDrift | Detects NP not applied |
| TestReconcileViolationDetection_ToolCountDrift | Detects tool count exceeding max |
| TestReconcileViolationDetection_CapsViolations | Ring-buffer capping at 100 |
| TestReconcileViolationDetection_MetricsIncrement | Prometheus counter incremented |
| TestReconcileViolationDetection_SetsCondition | ViolationDetected=True when violations |
| TestReconcileViolationDetection_ClearsCondition | ViolationDetected=False when resolved |
| TestReconcileViolationDetection_EnforcementModeDefault | Default to "alert" |
| TestReconcileViolationDetection_EnforcementModeQuarantine | Uses configured mode |

## Self-Check: PASSED

All artifacts verified:
- `packages/operator/api/v1alpha1/mcpprovider_types.go` contains ViolationRecord
- `packages/operator/pkg/metrics/metrics.go` contains CapabilityViolationsTotal
- `packages/operator/internal/controller/mcpprovider_controller.go` contains reconcileViolationDetection
- `packages/operator/internal/controller/violations_test.go` exists (244 lines)
- Commit fe979ca exists
- Commit eac7723 exists
