---
phase: 39-networkpolicy-generation
plan: 01
subsystem: operator
tags: [kubernetes, networkpolicy, go, tdd, enforcement]

# Dependency graph
requires:
  - phase: 38-capability-declaration
    provides: MCPProvider CRD with ProviderCapabilities, NetworkCapabilitiesSpec, EgressRuleSpec types
provides:
  - BuildNetworkPolicy pure function translating MCPProvider capabilities to K8s NetworkPolicy
  - NetworkPolicyName naming convention for egress policies
affects: [39-02, 39-03, operator-reconciler, enforcement-loop]

# Tech tracking
tech-stack:
  added: [k8s.io/api/networking/v1]
  patterns: [pure-function-builder, table-driven-tests, default-deny-egress]

key-files:
  created:
    - packages/operator/pkg/networkpolicy/builder.go
    - packages/operator/pkg/networkpolicy/builder_test.go
  modified: []

key-decisions:
  - "DNS allowed by default (nil DNSAllowed treated as true) to avoid breaking providers that need DNS resolution"
  - "Host-only rules (no CIDR) produce port-only NetworkPolicy rules with annotation warning rather than being silently dropped"
  - "Protocol mapping: https/http/grpc/tcp all map to TCP; 'any' omits protocol field"

patterns-established:
  - "Pure builder function: takes MCPProvider, returns *NetworkPolicy with no side effects or K8s client calls"
  - "Host-warnings annotation pattern: mcp-hangar.io/host-warnings for host-only rules lacking CIDR enforcement"

requirements-completed: [NP-BUILD]

# Metrics
duration: 3min
completed: 2026-03-24
---

# Phase 39 Plan 01: NetworkPolicyBuilder Summary

**Pure Go function translating MCPProvider capabilities into Kubernetes NetworkPolicy with default-deny egress, DNS allow, CIDR/host-only rules, loopback, and protocol mapping -- all TDD-driven with 15 test cases**

## Performance

- **Duration:** 3 min
- **Started:** 2026-03-24T19:11:31Z
- **Completed:** 2026-03-24T19:14:46Z
- **Tasks:** 1 (TDD: RED + GREEN, refactor skipped -- code already clean)
- **Files modified:** 2

## Accomplishments
- NetworkPolicyBuilder as a pure function with zero side effects, ready for reconciler consumption
- Comprehensive table-driven tests covering all 15 edge cases (nil caps, DNS, loopback, CIDR, host-only, port 0, protocol mapping, labels, pod selector)
- Host-only rules documented via annotation rather than silently dropped -- maintains security audit trail

## Task Commits

Each task was committed atomically:

1. **Task 1 (RED): Failing tests for NetworkPolicyBuilder** - `65577cf` (test)
2. **Task 1 (GREEN): Implement NetworkPolicyBuilder** - `eb184a6` (feat)

_Refactor phase skipped -- implementation was clean with no duplication._

## Files Created/Modified
- `packages/operator/pkg/networkpolicy/builder.go` - Pure function: BuildNetworkPolicy, NetworkPolicyName, protocol mapping, DNS/loopback rules, host warnings
- `packages/operator/pkg/networkpolicy/builder_test.go` - 15 table-driven tests covering all edge cases

## Decisions Made
- DNS allowed by default (nil DNSAllowed = true) to avoid breaking providers needing DNS
- Host-only rules produce port-only enforcement with mcp-hangar.io/host-warnings annotation
- Protocol mapping: https/http/grpc/tcp -> TCP; "any"/empty -> nil (any protocol)
- Labels use mcp-hangar.io/component: network-policy (distinct from provider builder's "provider" component)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- Minor: test helper `protocolPtr` conflicted with same-named function in implementation (same package). Removed from test file since implementation exports it. Fixed in GREEN phase before commit.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- BuildNetworkPolicy ready for reconciler integration in plan 03
- Plan 02 (OwnerReference helpers / NetworkPolicy lifecycle) can proceed independently
- All verification criteria pass: tests green, go vet clean, full operator builds

---
*Phase: 39-networkpolicy-generation*
*Completed: 2026-03-24*
