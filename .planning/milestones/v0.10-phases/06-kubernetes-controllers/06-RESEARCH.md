# Phase 6: Kubernetes Controllers - Research

**Researched:** 2026-03-01
**Domain:** Kubernetes controller-runtime reconcilers (Go), envtest integration testing
**Confidence:** HIGH

## Summary

This phase implements two new Kubernetes controllers -- MCPProviderGroupReconciler and MCPDiscoverySourceReconciler -- following the established MCPProviderReconciler pattern already in the codebase. The existing codebase provides a strong, consistent template: reconcile loop structure (fetch, deletion check, finalizer add, normal reconcile), condition management via custom SetCondition() methods, metrics integration via the `pkg/metrics` package, and optional Hangar client integration with nil-guard patterns.

The project uses controller-runtime v0.17.0 with Go 1.23, k8s.io/api v0.29.0, and testify for assertions. No envtest infrastructure exists yet -- the current test file (`controller_test.go`) has only basic unit tests for config defaults. CRD YAML files exist in `packages/helm-charts/mcp-hangar-operator/crds/` and can be referenced by envtest's `CRDDirectoryPaths`. The Makefile already has an `envtest` target with `setup-envtest` tool installation but KUBEBUILDER_ASSETS has never been resolved in practice.

The MCPProviderGroup controller is an aggregation controller: it lists MCPProvider CRs matching a label selector, counts states, evaluates health policy thresholds, and sets 3 conditions (Ready, Degraded, Available). The MCPDiscoverySource controller is a producer: it scans Kubernetes resources (namespaces, configmaps, services, annotated pods), creates/updates MCPProvider CRs with owner references and managed-by labels, and handles additive vs authoritative sync modes. Both controllers need cross-resource RBAC and must be registered in main.go alongside the existing MCPProviderReconciler.

**Primary recommendation:** Follow the existing MCPProviderReconciler structure exactly -- same package, same constants, same patterns. Create a shared `suite_test.go` for envtest setup, then write table-driven integration tests with testify assertions (matching the project's existing test style, not Ginkgo/Gomega).

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

- Group Ready condition is threshold-based: Ready=True when health policy thresholds are met (minHealthyPercentage or minHealthyCount from HealthPolicy spec)
- Degraded and Ready conditions can coexist: Ready=True + Degraded=True signals "working but impaired" (some providers down but thresholds still met)
- When label selector matches zero MCPProviders: Ready=Unknown with reason "NoProviders" -- signals the group cannot evaluate health yet
- Use three condition types: Ready, Degraded, and Available. Available=True when at least 1 provider can serve requests, even if the group is Degraded
- Immediate deletion: delete MCPProvider CRs as soon as they are no longer discovered. The MCPProvider's own finalizer handles graceful shutdown of the underlying workload
- Label-based ownership: use a label (e.g., mcp-hangar.io/managed-by: <discovery-source-name>) on created MCPProviders. Only delete providers with your own label -- never touch manually-created or other-source providers
- Additive mode never deletes: if a provider disappears from the source, the MCPProvider CR stays. Manual cleanup is the operator's job
- Overwrite spec drift: discovery controller owns the MCPProvider spec. If someone edits it manually, next sync overwrites it back to the template + discovered values
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

### Deferred Ideas (OUT OF SCOPE)

- None -- discussion stayed within phase scope
</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| K8S-01 | MCPProviderGroup controller reconciles groups with label-based MCPProvider selection | Label selector conversion via `metav1.LabelSelectorAsSelector()`, `client.MatchingLabelsSelector` for list operations. Existing `MCPProviderGroupSpec.Selector` is `*metav1.LabelSelector` -- standard k8s pattern. |
| K8S-02 | MCPProviderGroup controller aggregates member status (ready/degraded/dead counts) | `MCPProviderGroupStatus` already has ReadyCount, DegradedCount, ColdCount, DeadCount, ProviderCount fields. Iterate MCPProviderList items, switch on `.Status.State`. |
| K8S-03 | MCPProviderGroup controller evaluates health policies and reports conditions | `IsHealthy()` helper exists on MCPProviderGroupStatus. Three condition types: Ready, Degraded, Available. Zero-member groups get Ready=Unknown. SetCondition() method ready to use. |
| K8S-04 | MCPDiscoverySource controller implements 4 discovery modes | 4 DiscoveryType constants defined. Each mode maps to listing different k8s resources: namespaces (list Namespaces by label), ConfigMap (get specific ConfigMap, parse YAML), Annotations (list Pods/Services by label, read annotations), ServiceDiscovery (list Services by label, extract endpoints). |
| K8S-05 | MCPDiscoverySource controller supports additive and authoritative sync modes | DiscoveryModeAdditive/Authoritative constants defined. Label `mcp-hangar.io/managed-by` for ownership tracking. Authoritative deletes only own-labeled providers from successfully scanned sources. |
| K8S-06 | MCPDiscoverySource controller creates MCPProvider CRs with owner references and provider templates | `controllerutil.SetControllerReference()` pattern from MCPProviderReconciler. ProviderTemplateConfig has Metadata + Spec fields. Merge template with discovered values. |
| K8S-07 | Both controllers have envtest-based integration tests | envtest package from controller-runtime v0.17.0. CRD YAMLs at `../../helm-charts/mcp-hangar-operator/crds/`. setup-envtest tool already in Makefile. testify for assertions (project convention). |

<!-- markdownlint-disable MD055 MD056 -->
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| sigs.k8s.io/controller-runtime | v0.17.0 | Controller framework, envtest | Already in go.mod, MCPProviderReconciler uses it |
| k8s.io/api | v0.29.0 | Core k8s types (Pod, Service, ConfigMap, Namespace) | Already in go.mod |
| k8s.io/apimachinery | v0.29.0 | metav1 types, label selectors, errors | Already in go.mod |
| k8s.io/client-go | v0.29.0 | Event recorder, auth plugins | Already in go.mod |
| github.com/stretchr/testify | v1.11.1 | Test assertions (assert/require) | Already in go.mod, used in existing tests |
| github.com/prometheus/client_golang | v1.18.0 | Prometheus metrics | Already in go.mod, used in pkg/metrics |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| sigs.k8s.io/controller-runtime/pkg/envtest | v0.17.0 | Integration test environment | All envtest-based tests |
| k8s.io/apimachinery/pkg/labels | v0.29.0 | Label selector parsing | MCPProviderGroup label selection |
| sigs.k8s.io/controller-runtime/pkg/controller/controllerutil | v0.17.0 | Finalizer and owner reference helpers | Both controllers |
| sigs.k8s.io/yaml | v1.4.0 | YAML parsing | ConfigMap discovery mode (parsing provider definitions) |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| testify (assert/require) | ginkgo/gomega | ginkgo/gomega is in go.mod as indirect but NOT used in existing tests; testify is the project convention |
| Native Go testing.T | testify | Project already uses testify; keep consistent |

**Installation:**

```bash
# No new dependencies needed -- all libraries already in go.mod
# Just need to download envtest binaries:
make envtest
```

## Architecture Patterns

### Recommended Project Structure

```
packages/operator/
├── internal/controller/
│   ├── mcpprovider_controller.go          # Existing (PATTERN REFERENCE)
│   ├── mcpprovidergroup_controller.go     # NEW: Group reconciler
│   ├── mcpdiscoverysource_controller.go   # NEW: Discovery reconciler
│   ├── controller_test.go                 # Existing unit tests
│   ├── suite_test.go                      # NEW: envtest suite setup
│   ├── mcpprovidergroup_controller_test.go    # NEW: Group integration tests
│   └── mcpdiscoverysource_controller_test.go  # NEW: Discovery integration tests
├── cmd/operator/main.go                   # Wire new controllers here
└── ...
```

### Pattern 1: Reconcile Loop Structure (follow existing MCPProviderReconciler)

**What:** Every controller follows the same flow: Fetch -> deletion check -> finalizer add -> normal reconcile -> metrics counting
**When to use:** All controllers in this project
**Example:**

```go
// Source: packages/operator/internal/controller/mcpprovider_controller.go
func (r *MCPProviderGroupReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    logger := log.FromContext(ctx)
    startTime := time.Now()
    defer func() {
        metrics.ReconcileDuration.WithLabelValues("mcpprovidergroup").Observe(time.Since(startTime).Seconds())
    }()

    // 1. Fetch the resource
    group := &mcpv1alpha1.MCPProviderGroup{}
    if err := r.Get(ctx, req.NamespacedName, group); err != nil {
        if errors.IsNotFound(err) {
            return ctrl.Result{}, nil
        }
        metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "error").Inc()
        return ctrl.Result{}, err
    }

    // 2. Handle deletion
    if !group.ObjectMeta.DeletionTimestamp.IsZero() {
        return r.reconcileDelete(ctx, group)
    }

    // 3. Add finalizer if not present
    if !controllerutil.ContainsFinalizer(group, finalizerName) {
        controllerutil.AddFinalizer(group, finalizerName)
        if err := r.Update(ctx, group); err != nil {
            return ctrl.Result{}, err
        }
        return ctrl.Result{Requeue: true}, nil
    }

    // 4. Normal reconciliation
    result, err := r.reconcileNormal(ctx, group)
    if err != nil {
        metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "error").Inc()
    } else {
        metrics.ReconcileTotal.WithLabelValues("mcpprovidergroup", "success").Inc()
    }
    return result, err
}
```

### Pattern 2: Label-Based MCPProvider Selection (for Group controller)

**What:** Convert metav1.LabelSelector to a labels.Selector and list matching MCPProviders
**When to use:** MCPProviderGroup reconcileNormal -- selecting member providers
**Example:**

```go
// Source: k8s.io/apimachinery label selector API + controller-runtime client.List
func (r *MCPProviderGroupReconciler) listMatchingProviders(ctx context.Context, group *mcpv1alpha1.MCPProviderGroup) (*mcpv1alpha1.MCPProviderList, error) {
    selector, err := metav1.LabelSelectorAsSelector(group.Spec.Selector)
    if err != nil {
        return nil, fmt.Errorf("invalid label selector: %w", err)
    }

    providerList := &mcpv1alpha1.MCPProviderList{}
    if err := r.List(ctx, providerList,
        client.InNamespace(group.Namespace),
        client.MatchingLabelsSelector{Selector: selector},
    ); err != nil {
        return nil, err
    }
    return providerList, nil
}
```

### Pattern 3: Condition Evaluation with Coexisting States

**What:** Set Ready, Degraded, and Available independently based on provider counts
**When to use:** MCPProviderGroup status update
**Example:**

```go
func (r *MCPProviderGroupReconciler) evaluateConditions(group *mcpv1alpha1.MCPProviderGroup) {
    status := &group.Status

    // Zero members: Ready=Unknown
    if status.ProviderCount == 0 {
        status.SetCondition(ConditionReady, metav1.ConditionUnknown, "NoProviders", "No providers match selector")
        status.SetCondition(ConditionAvailable, metav1.ConditionFalse, "NoProviders", "No providers available")
        status.SetCondition(ConditionDegraded, metav1.ConditionFalse, "NoProviders", "")
        return
    }

    // Available: at least 1 ready provider
    if status.ReadyCount > 0 {
        status.SetCondition(ConditionAvailable, metav1.ConditionTrue, "ProvidersAvailable",
            fmt.Sprintf("%d provider(s) available", status.ReadyCount))
    } else {
        status.SetCondition(ConditionAvailable, metav1.ConditionFalse, "NoReadyProviders", "No providers in Ready state")
    }

    // Ready: threshold-based
    healthy := status.IsHealthy(group.Spec.HealthPolicy)
    if healthy {
        status.SetCondition(ConditionReady, metav1.ConditionTrue, "HealthyThresholdMet", "Health policy thresholds met")
    } else {
        status.SetCondition(ConditionReady, metav1.ConditionFalse, "HealthyThresholdNotMet", "Health policy thresholds not met")
    }

    // Degraded: any non-ready providers (can coexist with Ready=True)
    degradedOrDead := status.DegradedCount + status.DeadCount
    if degradedOrDead > 0 {
        status.SetCondition(ConditionDegraded, metav1.ConditionTrue, "ProvidersUnhealthy",
            fmt.Sprintf("%d provider(s) degraded or dead", degradedOrDead))
    } else {
        status.SetCondition(ConditionDegraded, metav1.ConditionFalse, "AllHealthy", "")
    }
}
```

### Pattern 4: Owner Reference and Managed-By Label (for Discovery controller)

**What:** Set controller owner reference AND managed-by label when creating MCPProvider CRs
**When to use:** MCPDiscoverySource creating providers
**Example:**

```go
const (
    LabelDiscoveryManagedBy = "mcp-hangar.io/managed-by"
)

func (r *MCPDiscoverySourceReconciler) createOrUpdateProvider(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource, discovered DiscoveredProviderInfo) error {
    provider := &mcpv1alpha1.MCPProvider{
        ObjectMeta: metav1.ObjectMeta{
            Name:      discovered.Name,
            Namespace: source.Namespace,
        },
    }

    _, err := controllerutil.CreateOrUpdate(ctx, r.Client, provider, func() error {
        // Set labels (including managed-by for ownership tracking)
        if provider.Labels == nil {
            provider.Labels = make(map[string]string)
        }
        provider.Labels[LabelDiscoveryManagedBy] = source.Name

        // Apply template labels
        if source.Spec.ProviderTemplate != nil && source.Spec.ProviderTemplate.Metadata != nil {
            for k, v := range source.Spec.ProviderTemplate.Metadata.Labels {
                provider.Labels[k] = v
            }
        }

        // Merge template spec with discovered values
        if source.Spec.ProviderTemplate != nil && source.Spec.ProviderTemplate.Spec != nil {
            provider.Spec = *source.Spec.ProviderTemplate.Spec.DeepCopy()
        }
        // Override with discovered specifics
        provider.Spec.Endpoint = discovered.Endpoint
        provider.Spec.Mode = discovered.Mode

        // Set owner reference (for GC)
        if source.ShouldSetController() {
            return controllerutil.SetControllerReference(source, provider, r.Scheme)
        }
        return nil
    })
    return err
}
```

### Pattern 5: Envtest Suite Setup (for integration tests)

**What:** TestMain-based envtest setup with testify (NOT Ginkgo)
**When to use:** All envtest-based controller integration tests
**Example:**

```go
// suite_test.go
package controller

import (
    "context"
    "os"
    "path/filepath"
    "testing"

    mcpv1alpha1 "github.com/mcp-hangar/mcp-hangar/operator/api/v1alpha1"
    "k8s.io/client-go/kubernetes/scheme"
    "k8s.io/client-go/rest"
    ctrl "sigs.k8s.io/controller-runtime"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/envtest"
    logf "sigs.k8s.io/controller-runtime/pkg/log"
    "sigs.k8s.io/controller-runtime/pkg/log/zap"
)

var (
    testEnv   *envtest.Environment
    k8sClient client.Client
    cfg       *rest.Config
    ctx       context.Context
    cancel    context.CancelFunc
)

func TestMain(m *testing.M) {
    logf.SetLogger(zap.New(zap.WriteTo(os.Stderr), zap.UseDevMode(true)))

    ctx, cancel = context.WithCancel(context.Background())

    testEnv = &envtest.Environment{
        CRDDirectoryPaths:     []string{filepath.Join("..", "..", "..", "helm-charts", "mcp-hangar-operator", "crds")},
        ErrorIfCRDPathMissing: true,
    }

    var err error
    cfg, err = testEnv.Start()
    if err != nil {
        panic(err)
    }

    err = mcpv1alpha1.AddToScheme(scheme.Scheme)
    if err != nil {
        panic(err)
    }

    k8sClient, err = client.New(cfg, client.Options{Scheme: scheme.Scheme})
    if err != nil {
        panic(err)
    }

    // Start controllers under test
    mgr, err := ctrl.NewManager(cfg, ctrl.Options{
        Scheme: scheme.Scheme,
    })
    if err != nil {
        panic(err)
    }

    // Register controllers
    err = (&MCPProviderGroupReconciler{
        Client:   mgr.GetClient(),
        Scheme:   mgr.GetScheme(),
        Recorder: mgr.GetEventRecorderFor("mcpprovidergroup-controller"),
    }).SetupWithManager(mgr)
    if err != nil {
        panic(err)
    }

    err = (&MCPDiscoverySourceReconciler{
        Client:   mgr.GetClient(),
        Scheme:   mgr.GetScheme(),
        Recorder: mgr.GetEventRecorderFor("mcpdiscoverysource-controller"),
    }).SetupWithManager(mgr)
    if err != nil {
        panic(err)
    }

    go func() {
        if err := mgr.Start(ctx); err != nil {
            panic(err)
        }
    }()

    code := m.Run()

    cancel()
    err = testEnv.Stop()
    if err != nil {
        panic(err)
    }
    os.Exit(code)
}
```

### Pattern 6: Authoritative Sync with Scoped Deletion

**What:** Only delete MCPProviders from successfully-scanned sources that no longer appear
**When to use:** MCPDiscoverySource with authoritative mode
**Example:**

```go
func (r *MCPDiscoverySourceReconciler) authoritativeSync(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource, discovered map[string]DiscoveredProviderInfo) error {
    // List all MCPProviders managed by this source
    existingList := &mcpv1alpha1.MCPProviderList{}
    if err := r.List(ctx, existingList,
        client.InNamespace(source.Namespace),
        client.MatchingLabels{LabelDiscoveryManagedBy: source.Name},
    ); err != nil {
        return err
    }

    // Delete providers that were NOT discovered (from successfully-scanned sources only)
    for _, existing := range existingList.Items {
        if _, found := discovered[existing.Name]; !found {
            if err := r.Delete(ctx, &existing); err != nil && !errors.IsNotFound(err) {
                return err
            }
        }
    }

    return nil
}
```

### Pattern 7: SetupWithManager with Watches (for cross-resource reconciliation)

**What:** MCPProviderGroup must re-reconcile when MCPProvider resources change
**When to use:** Group controller setup
**Example:**

```go
func (r *MCPProviderGroupReconciler) SetupWithManager(mgr ctrl.Manager) error {
    return ctrl.NewControllerManagedBy(mgr).
        For(&mcpv1alpha1.MCPProviderGroup{}).
        Watches(
            &mcpv1alpha1.MCPProvider{},
            handler.EnqueueRequestsFromMapFunc(r.findGroupsForProvider),
        ).
        Complete(r)
}

func (r *MCPProviderGroupReconciler) findGroupsForProvider(ctx context.Context, obj client.Object) []ctrl.Request {
    // Find all groups that might select this provider
    groupList := &mcpv1alpha1.MCPProviderGroupList{}
    if err := r.List(ctx, groupList, client.InNamespace(obj.GetNamespace())); err != nil {
        return nil
    }

    var requests []ctrl.Request
    provider, ok := obj.(*mcpv1alpha1.MCPProvider)
    if !ok {
        return nil
    }

    for _, group := range groupList.Items {
        selector, err := metav1.LabelSelectorAsSelector(group.Spec.Selector)
        if err != nil {
            continue
        }
        if selector.Matches(labels.Set(provider.Labels)) {
            requests = append(requests, ctrl.Request{
                NamespacedName: types.NamespacedName{
                    Name:      group.Name,
                    Namespace: group.Namespace,
                },
            })
        }
    }
    return requests
}
```

### Anti-Patterns to Avoid

- **Direct client calls in reconcile for cross-resource creation without owner references:** Always set owner references when creating MCPProviders from discovery. Without them, deleting the MCPDiscoverySource would orphan MCPProvider CRs.
- **Deleting providers from failed discovery scans in authoritative mode:** If a namespace is inaccessible, do NOT treat its providers as "gone". Only providers from successfully scanned sources are eligible for deletion.
- **Mixing Ginkgo with testify in the same test suite:** The project uses testify exclusively. Introducing Ginkgo would create inconsistency.
- **Blocking reconcile on discovery operations:** Each discovery mode (namespace scan, configmap read, etc.) should be a separate function that can fail independently while other modes succeed (partial sync tolerance).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Label selector matching | Custom label comparison logic | `metav1.LabelSelectorAsSelector()` + `client.MatchingLabelsSelector` | Handles matchLabels and matchExpressions correctly |
| Owner reference management | Manual OwnerReference construction | `controllerutil.SetControllerReference()` | Handles UID, GVK, blockOwnerDeletion correctly |
| Finalizer management | Manual finalizer slice manipulation | `controllerutil.AddFinalizer()` / `RemoveFinalizer()` / `ContainsFinalizer()` | Idempotent, handles edge cases |
| Create-or-update logic | Manual Get/Create/Update flows | `controllerutil.CreateOrUpdate()` | Atomic, handles conflict retries |
| Condition management | Custom condition array logic | Existing `SetCondition()` methods on status types | Already implemented on MCPProviderGroupStatus and MCPDiscoverySourceStatus |
| Test API server | Mock k8s client | `envtest.Environment` | Real API server, tests RBAC/validation/webhooks, catches serialization bugs |
| YAML parsing for ConfigMap discovery | Custom YAML parser | `sigs.k8s.io/yaml.Unmarshal()` | Already a dependency, handles k8s-style YAML |

**Key insight:** controller-runtime provides battle-tested utilities for nearly every Kubernetes controller operation. Hand-rolling any of these introduces subtle bugs around resource versioning, GC, and concurrency.

## Common Pitfalls

### Pitfall 1: Forgetting Watches on Cross-Referenced Resources

**What goes wrong:** MCPProviderGroup only reconciles on its own spec changes; does not react when an MCPProvider changes state (e.g., becomes Ready/Degraded).
**Why it happens:** `ctrl.NewControllerManagedBy(mgr).For(&MCPProviderGroup{}).Complete(r)` only watches the primary resource.
**How to avoid:** Add `.Watches(&MCPProvider{}, handler.EnqueueRequestsFromMapFunc(findGroupsForProvider))` to trigger group reconciliation when any matching provider changes.
**Warning signs:** Group status shows stale counts even after provider states change.

### Pitfall 2: KUBEBUILDER_ASSETS Not Set for envtest

**What goes wrong:** `go test` fails with "unable to start control plane" because etcd/kube-apiserver binaries are not found.
**Why it happens:** envtest needs KUBEBUILDER_ASSETS environment variable pointing to the binary directory. The Makefile has the target but it has never been used.
**How to avoid:** Use `setup-envtest` to download binaries: `KUBEBUILDER_ASSETS="$(shell $(ENVTEST) use $(ENVTEST_K8S_VERSION) --bin-dir $(LOCALBIN) -p path)"` (already in Makefile's test target).
**Warning signs:** Tests fail immediately before any test function runs.

### Pitfall 3: Status Update Conflicts

**What goes wrong:** `r.Status().Update(ctx, obj)` fails with "the object has been modified" (409 Conflict) because the resource was modified between Get and Status Update.
**Why it happens:** Another reconciliation or controller updated the resource concurrently.
**How to avoid:** Return the error from Status().Update() and let controller-runtime re-queue. Do NOT use r.Get() to refresh in the middle of reconcile -- let the full reconcile restart.
**Warning signs:** Frequent "conflict" errors in operator logs.

### Pitfall 4: Authoritative Deletion Race Condition

**What goes wrong:** Discovery source scan partially fails, authoritative sync treats "not discovered" as "deleted", removing providers that still exist but were in a failed scan.
**Why it happens:** Not scoping deletion to successfully-scanned sources.
**How to avoid:** Track which sources were successfully scanned. Only consider providers from those sources as candidates for deletion. This is a locked decision in CONTEXT.md.
**Warning signs:** Providers disappear and reappear on subsequent reconcile cycles.

### Pitfall 5: Missing RBAC Markers

**What goes wrong:** Controller fails at runtime with "forbidden" errors when trying to list MCPProviders from the group controller.
**Why it happens:** RBAC markers only grant access to the primary resource. Cross-resource operations (group listing providers, discovery creating providers) need explicit RBAC grants.
**How to avoid:** Add kubebuilder RBAC markers for ALL resources the controller touches. Group controller needs: mcpproviders (list/watch), mcpprovidergroups (get/list/watch/update/patch), mcpprovidergroups/status (get/update/patch). Discovery controller needs: mcpproviders (get/list/watch/create/update/patch/delete), mcpdiscoverysources (get/list/watch/update/patch), plus core resources (namespaces, configmaps, services, pods) for discovery scanning.
**Warning signs:** `make manifests` generates incomplete ClusterRole.

### Pitfall 6: CRD Path for envtest

**What goes wrong:** envtest starts but CRDs are not installed, causing 404 errors when creating custom resources.
**Why it happens:** Wrong relative path to CRD YAML directory from test file location.
**How to avoid:** CRDs are at `packages/helm-charts/mcp-hangar-operator/crds/`. From `internal/controller/`, the relative path is `filepath.Join("..", "..", "..", "..", "helm-charts", "mcp-hangar-operator", "crds")`. Verify by running `ls` on the resolved path during test development.
**Warning signs:** Tests pass locally with absolute path but fail in CI.

### Pitfall 7: Discovery Controller Paused State

**What goes wrong:** Paused discovery source still creates/deletes providers because the paused check is in the wrong place.
**Why it happens:** Checking paused state after some operations have already started.
**How to avoid:** Check `source.IsPaused()` as the FIRST thing in reconcileNormal. If paused, set Paused condition, skip everything, and return with a long requeue interval.
**Warning signs:** Provider churn continues even after setting `spec.paused: true`.

## Code Examples

Verified patterns from existing codebase and controller-runtime:

### Reconciler Struct (consistent with MCPProviderReconciler)

```go
// Source: modeled after packages/operator/internal/controller/mcpprovider_controller.go
type MCPProviderGroupReconciler struct {
    client.Client
    Scheme       *runtime.Scheme
    Recorder     record.EventRecorder
    HangarClient *hangar.Client  // Optional, nil-checked
}

type MCPDiscoverySourceReconciler struct {
    client.Client
    Scheme       *runtime.Scheme
    Recorder     record.EventRecorder
    HangarClient *hangar.Client  // Optional, nil-checked
}
```

### Controller Registration in main.go

```go
// Source: modeled after packages/operator/cmd/operator/main.go lines 94-103
// After existing MCPProviderReconciler setup:

if err = (&controller.MCPProviderGroupReconciler{
    Client:       mgr.GetClient(),
    Scheme:       mgr.GetScheme(),
    Recorder:     mgr.GetEventRecorderFor("mcpprovidergroup-controller"),
    HangarClient: hangarClient,
}).SetupWithManager(mgr); err != nil {
    setupLog.Error(err, "unable to create controller", "controller", "MCPProviderGroup")
    os.Exit(1)
}

if err = (&controller.MCPDiscoverySourceReconciler{
    Client:       mgr.GetClient(),
    Scheme:       mgr.GetScheme(),
    Recorder:     mgr.GetEventRecorderFor("mcpdiscoverysource-controller"),
    HangarClient: hangarClient,
}).SetupWithManager(mgr); err != nil {
    setupLog.Error(err, "unable to create controller", "controller", "MCPDiscoverySource")
    os.Exit(1)
}
```

### RBAC Markers for Group Controller

```go
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpprovidergroups/finalizers,verbs=update
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpproviders,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
```

### RBAC Markers for Discovery Controller

```go
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpdiscoverysources/finalizers,verbs=update
// +kubebuilder:rbac:groups=mcp-hangar.io,resources=mcpproviders,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=namespaces,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=services,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=pods,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
```

### Integration Test Example (testify style)

```go
func TestMCPProviderGroupReconciler_AggregatesStatus(t *testing.T) {
    ns := "test-group-" + randString(5)
    createNamespace(t, ns)

    // Create MCPProviders with labels
    provider1 := newMCPProvider(ns, "provider-1", map[string]string{"app": "test"})
    provider1.Status.State = mcpv1alpha1.ProviderStateReady
    require.NoError(t, k8sClient.Create(ctx, provider1))
    require.NoError(t, k8sClient.Status().Update(ctx, provider1))

    provider2 := newMCPProvider(ns, "provider-2", map[string]string{"app": "test"})
    provider2.Status.State = mcpv1alpha1.ProviderStateDegraded
    require.NoError(t, k8sClient.Create(ctx, provider2))
    require.NoError(t, k8sClient.Status().Update(ctx, provider2))

    // Create MCPProviderGroup selecting by label
    group := newMCPProviderGroup(ns, "test-group", map[string]string{"app": "test"})
    require.NoError(t, k8sClient.Create(ctx, group))

    // Wait for reconciliation
    assert.Eventually(t, func() bool {
        err := k8sClient.Get(ctx, client.ObjectKeyFromObject(group), group)
        return err == nil && group.Status.ProviderCount == 2
    }, 10*time.Second, 250*time.Millisecond)

    assert.Equal(t, int32(1), group.Status.ReadyCount)
    assert.Equal(t, int32(1), group.Status.DegradedCount)
}
```

### Metrics Integration (using existing pkg/metrics)

```go
// Group controller metrics update
func (r *MCPProviderGroupReconciler) updateMetrics(group *mcpv1alpha1.MCPProviderGroup) {
    ns, name := group.Namespace, group.Name
    metrics.GroupProviderCount.WithLabelValues(ns, name, "Ready").Set(float64(group.Status.ReadyCount))
    metrics.GroupProviderCount.WithLabelValues(ns, name, "Degraded").Set(float64(group.Status.DegradedCount))
    metrics.GroupProviderCount.WithLabelValues(ns, name, "Cold").Set(float64(group.Status.ColdCount))
    metrics.GroupProviderCount.WithLabelValues(ns, name, "Dead").Set(float64(group.Status.DeadCount))
}

// Discovery controller metrics update
func (r *MCPDiscoverySourceReconciler) updateMetrics(source *mcpv1alpha1.MCPDiscoverySource, duration time.Duration) {
    ns, name := source.Namespace, source.Name
    metrics.DiscoverySourceCount.WithLabelValues(ns, name).Set(float64(source.Status.DiscoveredCount))
    metrics.DiscoverySyncDuration.WithLabelValues(ns, name).Observe(duration.Seconds())
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Ginkgo/Gomega for controller tests | testify is fine; Ginkgo is not required | Always | This project chose testify; either works with envtest |
| `handler.EnqueueRequestForOwner` only | `handler.EnqueueRequestsFromMapFunc` for non-owned cross-references | controller-runtime v0.15+ | Group controller needs MapFunc since it does not own MCPProviders |
| `ctrl.Options{MetricsBindAddress: "0"}` | `ctrl.Options{Metrics: metricsserver.Options{BindAddress: "0"}}` | controller-runtime v0.17.0 | Old field removed; test manager setup must use new struct |
| `Watches(&source{}, &handler{})` | `Watches(&MCPProvider{}, handler.EnqueueRequestsFromMapFunc(fn))` | controller-runtime v0.15+ | Simplified API, source.Kind{} wrapper removed |

**Deprecated/outdated:**

- `source.Kind{}` wrapper for Watches: removed in controller-runtime v0.15+, pass object directly
- `MetricsBindAddress` field on ctrl.Options: removed, use `Metrics: metricsserver.Options{}` struct

## Open Questions

1. **CRD YAML path relative to test files**
   - What we know: CRDs are at `packages/helm-charts/mcp-hangar-operator/crds/`. Test files will be at `packages/operator/internal/controller/`.
   - What's unclear: The exact relative path depends on how go test resolves the working directory. Go test runs from the package directory.
   - Recommendation: Use `filepath.Join("..", "..", "..", "..", "helm-charts", "mcp-hangar-operator", "crds")` from `internal/controller/`. Verify in the first implementation wave.

2. **ConfigMap discovery YAML schema**
   - What we know: ConfigMap discovery reads from `spec.configMapRef.key` (default "providers.yaml") within a ConfigMap.
   - What's unclear: The exact YAML schema within the ConfigMap for defining provider specifications. Need to define a schema that maps to MCPProviderSpec.
   - Recommendation: Use a simple map of provider names to MCPProviderSpec-compatible YAML. Define a `ConfigMapProviderDefinition` struct for unmarshalling.

3. **Requeue intervals for new controllers**
   - What we know: Existing MCPProviderReconciler uses defaultRequeueAfter=30s, errorRequeueAfter=10s, readyRequeueAfter=5min, coldRequeueAfter=10min.
   - What's unclear: Optimal intervals for group and discovery controllers.
   - Recommendation: Group controller: 30s default, uses readyRequeueAfter (5min) when stable. Discovery controller: uses spec.refreshInterval (default "1m") as requeue after successful sync, errorRequeueAfter (10s) on failure.

## Sources

### Primary (HIGH confidence)

- Codebase: `packages/operator/internal/controller/mcpprovider_controller.go` -- complete pattern reference
- Codebase: `packages/operator/api/v1alpha1/` -- all type definitions, helper methods, SetCondition
- Codebase: `packages/operator/pkg/metrics/metrics.go` -- pre-defined metrics for groups and discovery
- Codebase: `packages/operator/cmd/operator/main.go` -- controller registration pattern
- Codebase: `packages/operator/Makefile` -- envtest setup target, ENVTEST_K8S_VERSION=1.29.0
- Codebase: `packages/operator/go.mod` -- controller-runtime v0.17.0, k8s v0.29.0, testify v1.11.1
- pkg.go.dev: `sigs.k8s.io/controller-runtime@v0.17.0/pkg/envtest` -- Environment API

### Secondary (MEDIUM confidence)

- controller-runtime examples at github.com/kubernetes-sigs/controller-runtime/tree/v0.17.0/examples -- reconciler patterns
- envtest documentation on pkg.go.dev -- Environment struct, CRDInstallOptions

### Tertiary (LOW confidence)

- None -- all findings verified against codebase or official documentation

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH -- all libraries already in go.mod, versions pinned
- Architecture: HIGH -- following existing MCPProviderReconciler pattern exactly, all types defined
- Pitfalls: HIGH -- based on direct codebase analysis and known controller-runtime patterns
- Envtest setup: MEDIUM -- envtest is well-documented but has not been set up in this project before; path resolution needs verification

**Research date:** 2026-03-01
**Valid until:** 2026-04-01 (stable -- no dependency updates expected in v0.10 window)
