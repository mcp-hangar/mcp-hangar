<!-- markdownlint-disable MD001 -->
# Phase 6: Kubernetes Controllers - Context

**Gathered:** 2026-02-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement MCPProviderGroup and MCPDiscoverySource Kubernetes controllers (reconcilers) with full test coverage. The CRD types, Spec/Status structs, and DeepCopy methods already exist. This phase builds the reconciliation logic, wires controllers into the operator main.go, and validates with envtest-based integration tests. Helm chart updates are Phase 7.

</domain>

<decisions>
## Implementation Decisions

### Group health conditions

- Group Ready condition is threshold-based: Ready=True when health policy thresholds are met (minHealthyPercentage or minHealthyCount from HealthPolicy spec)
- Degraded and Ready conditions can coexist: Ready=True + Degraded=True signals "working but impaired" (some providers down but thresholds still met)
- When label selector matches zero MCPProviders: Ready=Unknown with reason "NoProviders" -- signals the group cannot evaluate health yet
- Use three condition types: Ready, Degraded, and Available. Available=True when at least 1 provider can serve requests, even if the group is Degraded

### Authoritative sync deletion

- Immediate deletion: delete MCPProvider CRs as soon as they are no longer discovered. The MCPProvider's own finalizer handles graceful shutdown of the underlying workload
- Label-based ownership: use a label (e.g., mcp-hangar.io/managed-by: <discovery-source-name>) on created MCPProviders. Only delete providers with your own label -- never touch manually-created or other-source providers
- Additive mode never deletes: if a provider disappears from the source, the MCPProvider CR stays. Manual cleanup is the operator's job
- Overwrite spec drift: discovery controller owns the MCPProvider spec. If someone edits it manually, next sync overwrites it back to the template + discovered values

### Discovery error tolerance

- Partial sync with error reporting: skip failing sources (inaccessible namespace, malformed ConfigMap) and continue syncing whatever succeeded. Report partial failure in status conditions
- Authoritative deletion scoped to successful scans: providers from inaccessible sources are NOT considered "gone." Only providers from successfully-scanned sources are eligible for deletion
- Error surfacing via Synced condition: set Synced=False with error details and populate status.lastSyncError. Keep Ready condition reflecting overall health independently
- Paused means full freeze: no scans, no creates, no deletes. Existing MCPProvider CRs stay as-is. Status shows Paused=True condition. Resume picks up from current state

### Claude's Discretion

- Envtest setup and test infrastructure details
- Exact requeue intervals for each controller
- RBAC marker specifics for each controller
- Internal reconcile loop structure (following MCPProviderReconciler pattern)
- Finalizer cleanup logic details
- Hangar client integration decisions (optional dependency pattern)

</decisions>

<code_context>

## Existing Code Insights

### Reusable Assets

- MCPProviderReconciler (internal/controller/mcpprovider_controller.go): Complete pattern reference for reconcile loop, finalizers, conditions, Pod management, metrics, and Hangar client integration
- SetCondition() method: Already defined on MCPProviderGroupStatus and MCPDiscoverySourceStatus -- ready to use
- Hangar client (pkg/hangar/client.go): RegisterProvider, DeregisterProvider, GetProviderTools, HealthCheckRemote APIs available
- Provider builder (pkg/provider/builder.go): Pod construction with security defaults, labels, and annotations
- Pre-defined metrics (pkg/metrics/metrics.go): GroupProviderCount (gauge with state label), DiscoverySourceCount (gauge), DiscoverySyncDuration (histogram), ClearGroupMetrics(), ClearDiscoveryMetrics() helpers

### Established Patterns

- Reconcile loop: Fetch -> deletion check -> finalizer add -> normal reconcile -> metrics counting
- Finalizer name: "mcp-hangar.io/finalizer" -- used consistently across all controllers
- Condition types: ConditionReady, ConditionProgressing, ConditionDegraded, ConditionAvailable (constants defined)
- Owner references: controllerutil.SetControllerReference() for parent-child relationships
- Hangar client: nil-check guard pattern (if r.HangarClient != nil) -- always optional
- Error handling: infrastructure errors return (Result{}, err) for controller-runtime backoff; validation errors set condition and return (Result{}, nil) with no requeue; transient errors use RequeueAfter
- Requeue intervals: defaultRequeueAfter=30s, errorRequeueAfter=10s, readyRequeueAfter=5min, coldRequeueAfter=10min
- Test patterns: testify (assert + require), httptest.NewServer for Hangar client mocking, table-driven tests
- Labels: app.kubernetes.io/*standard labels + mcp-hangar.io/* custom labels

### Integration Points

- cmd/operator/main.go (line 94-103): New controllers must be registered after MCPProviderReconciler, following the same pattern
- RBAC markers: New controllers need kubebuilder RBAC comments for their resources + cross-resource access (groups need to list mcpproviders, discovery needs to create mcpproviders)
- Makefile envtest target: exists but envtest was never set up -- needs KUBEBUILDER_ASSETS and setup-envtest binary
- Scheme registration (main.go line 29-32): Already registers mcpv1alpha1 -- no changes needed for new CRD types

</code_context>

<specifics>
## Specific Ideas

No specific requirements -- open to standard Kubernetes controller patterns. Follow the established MCPProviderReconciler conventions for consistency.

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 06-kubernetes-controllers*
*Context gathered: 2026-02-28*
