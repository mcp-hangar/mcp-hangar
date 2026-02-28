# Architecture Patterns

**Domain:** Kubernetes operator controllers and documentation infrastructure for MCP Hangar v0.10
**Researched:** 2026-02-28
**Confidence:** HIGH (based on existing codebase analysis + controller-runtime established patterns)

## System Overview

The v0.10 architecture adds two new controllers (MCPProviderGroup, MCPDiscoverySource) to an existing controller-runtime operator that already has one working controller (MCPProvider). The key challenge is cross-resource reconciliation: MCPProviderGroup must aggregate MCPProvider status via label selectors, and MCPDiscoverySource must create/manage MCPProvider custom resources as a parent. Additionally, four MkDocs reference pages are added to the existing documentation site.

### Current Architecture

```
cmd/operator/main.go          -- Composition root, wires controllers to manager
internal/controller/           -- Controller implementations (currently MCPProvider only)
api/v1alpha1/                  -- CRD types (all 3 defined, deepcopy generated)
pkg/hangar/                    -- HTTP client to Python core
pkg/metrics/                   -- Prometheus metrics (all 3 controllers pre-defined)
pkg/provider/                  -- Pod builder for container-mode providers
```

### Target Architecture (v0.10)

```
cmd/operator/main.go           -- Add MCPProviderGroup + MCPDiscoverySource controller setup
internal/controller/
    mcpprovider_controller.go          -- EXISTING (no changes needed)
    mcpprovidergroup_controller.go     -- NEW: label-select MCPProviders, aggregate status
    mcpdiscoverysource_controller.go   -- NEW: discover, create/manage MCPProvider CRs
pkg/hangar/client.go           -- EXISTING: may need RegisterGroup/SyncGroup methods
pkg/metrics/metrics.go         -- EXISTING: metrics already defined for all 3 controllers
pkg/provider/builder.go        -- EXISTING (no changes needed)
pkg/discovery/                 -- NEW: discovery strategy implementations
    namespace.go               -- Namespace-scanning discovery
    configmap.go               -- ConfigMap-based discovery
    annotations.go             -- Annotation-based discovery
    service.go                 -- Service discovery
    interface.go               -- DiscoveryStrategy interface
```

## Component Responsibilities

### MCPProviderGroup Controller (NEW)

| Aspect | Detail |
|--------|--------|
| **Primary resource** | `MCPProviderGroup` |
| **Watches** | `MCPProvider` (via label selector match) |
| **Owns** | Nothing -- MCPProviders are independent resources, not owned by groups |
| **Responsibility** | Select MCPProviders via `spec.selector`, aggregate their status into group status, evaluate health policy, update conditions |
| **Requeue triggers** | Own spec change, MCPProvider status change (for matching providers) |
| **HangarClient usage** | Optional: register/sync group config with Python core for load balancing |

### MCPDiscoverySource Controller (NEW)

| Aspect | Detail |
|--------|--------|
| **Primary resource** | `MCPDiscoverySource` |
| **Watches** | Pods, Services, ConfigMaps, Namespaces (depending on discovery type) |
| **Owns** | `MCPProvider` CRs it creates (controller owner reference) |
| **Responsibility** | Scan Kubernetes resources per discovery type, create MCPProvider CRs, manage lifecycle in authoritative mode, track discovered providers in status |
| **Requeue triggers** | Own spec change, timer-based rescan (refreshInterval), watched resource changes |
| **HangarClient usage** | None directly -- created MCPProviders trigger the MCPProvider controller |

### Existing MCPProvider Controller (UNCHANGED)

No modifications required. MCPProviders created by MCPDiscoverySource are indistinguishable from manually created ones -- the controller reconciles them identically. Owner references on discovery-created MCPProviders enable garbage collection when the MCPDiscoverySource is deleted.

### Documentation Pages (NEW)

| Page | Content Source | MkDocs Plugin |
|------|---------------|---------------|
| Configuration Reference | Manual YAML schema documentation | None (pure markdown) |
| MCP Tools Reference | Manual from `server/tools/` Python code | mkdocstrings (Python handler) |
| Provider Groups Guide | Manual with CRD examples | None (pure markdown) |
| Facade API Guide | Manual with code examples | mkdocstrings (Python handler) |

## Architectural Patterns

### Pattern 1: Cross-Resource Watch with EnqueueRequestsFromMapFunc

**What:** MCPProviderGroup needs to re-reconcile when MCPProvider resources change. Since MCPProviderGroup does not own MCPProviders (groups are an overlay, not a parent), use `handler.EnqueueRequestsFromMapFunc` to map MCPProvider changes to the owning MCPProviderGroup(s).

**When:** Any time a controller needs to react to changes in resources it does not own.

**Why:** This is the canonical controller-runtime pattern for cross-resource watches without ownership. The Watches builder method with a custom MapFunc lets you list all MCPProviderGroups, check which ones' selectors match the changed MCPProvider, and enqueue those groups for reconciliation.

```go
func (r *MCPProviderGroupReconciler) SetupWithManager(mgr ctrl.Manager) error {
    return ctrl.NewControllerManagedBy(mgr).
        For(&mcpv1alpha1.MCPProviderGroup{}).
        Watches(
            &mcpv1alpha1.MCPProvider{},
            handler.EnqueueRequestsFromMapFunc(r.mapProviderToGroups),
        ).
        Complete(r)
}

func (r *MCPProviderGroupReconciler) mapProviderToGroups(ctx context.Context, obj client.Object) []reconcile.Request {
    provider, ok := obj.(*mcpv1alpha1.MCPProvider)
    if !ok {
        return nil
    }

    // List all MCPProviderGroups in the same namespace
    var groupList mcpv1alpha1.MCPProviderGroupList
    if err := r.List(ctx, &groupList, client.InNamespace(provider.Namespace)); err != nil {
        return nil
    }

    var requests []reconcile.Request
    for _, group := range groupList.Items {
        selector, err := metav1.LabelSelectorAsSelector(group.Spec.Selector)
        if err != nil {
            continue
        }
        if selector.Matches(labels.Set(provider.Labels)) {
            requests = append(requests, reconcile.Request{
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

**Confidence:** HIGH -- this is the standard controller-runtime pattern documented in the Kubebuilder book and used by operators like Prometheus Operator (ServiceMonitor -> Prometheus mapping).

### Pattern 2: Owner Reference for Discovery-Created Resources

**What:** MCPDiscoverySource creates MCPProvider CRs and sets itself as the controller owner. This enables automatic garbage collection: when the MCPDiscoverySource is deleted, all MCPProviders it created are also deleted.

**When:** A controller creates child custom resources that should be cleaned up with the parent.

```go
// In MCPDiscoverySource reconciler, when creating an MCPProvider:
mcpProvider := &mcpv1alpha1.MCPProvider{
    ObjectMeta: metav1.ObjectMeta{
        Name:      providerName,
        Namespace: discoverySource.Namespace,
        Labels: mergeLabels(
            discoverySource.Spec.ProviderTemplate.Metadata.Labels,
            map[string]string{
                "mcp-hangar.io/discovery-source": discoverySource.Name,
            },
        ),
    },
    Spec: buildProviderSpec(discovered, discoverySource.Spec.ProviderTemplate),
}

if discoverySource.ShouldSetController() {
    if err := controllerutil.SetControllerReference(discoverySource, mcpProvider, r.Scheme); err != nil {
        return err
    }
}
```

**Confidence:** HIGH -- this follows the exact same pattern the MCPProvider controller already uses for Pods.

### Pattern 3: Authoritative Mode with Adopt/Orphan Semantics

**What:** In Authoritative mode, MCPDiscoverySource creates MCPProviders for discovered sources AND deletes MCPProviders that are no longer discovered. In Additive mode, it only creates, never deletes.

**When:** A discovery source should act as the single source of truth for a set of providers.

```go
func (r *MCPDiscoverySourceReconciler) reconcileAuthoritative(
    ctx context.Context,
    source *mcpv1alpha1.MCPDiscoverySource,
    discovered []DiscoveredProvider,
) error {
    // List existing MCPProviders owned by this source
    var existing mcpv1alpha1.MCPProviderList
    if err := r.List(ctx, &existing,
        client.InNamespace(source.Namespace),
        client.MatchingLabels{"mcp-hangar.io/discovery-source": source.Name},
    ); err != nil {
        return err
    }

    // Build sets for diff
    discoveredSet := toNameSet(discovered)
    existingSet := toNameSet(existing.Items)

    // Create new
    for _, d := range discovered {
        if !existingSet.Has(d.Name) {
            // Create MCPProvider
        }
    }

    // Delete removed (authoritative only)
    for _, e := range existing.Items {
        if !discoveredSet.Has(e.Name) {
            if err := r.Delete(ctx, &e); err != nil {
                // Log, continue
            }
        }
    }

    return nil
}
```

**Confidence:** HIGH -- standard set-reconciliation pattern used by ArgoCD ApplicationSets, Flux HelmReleases.

### Pattern 4: Discovery Strategy Interface

**What:** Each discovery type (Namespace, ConfigMap, Annotations, ServiceDiscovery) implements a common interface, selected by the `spec.type` field. This keeps the controller focused on lifecycle management while strategies handle the scanning logic.

```go
// pkg/discovery/interface.go
type DiscoveryStrategy interface {
    Discover(ctx context.Context, source *mcpv1alpha1.MCPDiscoverySource) ([]DiscoveredProvider, error)
}

type DiscoveredProvider struct {
    Name     string
    Source   string
    Mode     mcpv1alpha1.ProviderMode
    Image    string
    Endpoint string
    Labels   map[string]string
}
```

**Confidence:** HIGH -- direct analogue of the existing `provider.BuildPodForProvider` separation: business logic in pkg, reconciliation in internal/controller.

### Pattern 5: Periodic Rescan via RequeueAfter

**What:** MCPDiscoverySource needs to periodically rescan for new providers. Use `ctrl.Result{RequeueAfter: refreshInterval}` rather than a separate ticker goroutine. This keeps the rescan within the controller's reconciliation loop, getting automatic leader-election awareness and rate limiting.

**When:** A controller needs periodic polling behavior.

```go
func (r *MCPDiscoverySourceReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
    // ... discovery logic ...

    refreshInterval, _ := time.ParseDuration(source.Spec.RefreshInterval)
    if refreshInterval == 0 {
        refreshInterval = time.Minute // default
    }

    return ctrl.Result{RequeueAfter: refreshInterval}, nil
}
```

**Confidence:** HIGH -- the MCPProvider controller already uses this pattern (readyRequeueAfter, coldRequeueAfter).

## Anti-Patterns to Avoid

### Anti-Pattern 1: MCPProviderGroup Owning MCPProviders

**What:** Setting MCPProviderGroup as owner of the MCPProviders it selects.
**Why bad:** MCPProviders are independent resources that may belong to multiple groups (via label selectors) or no group at all. Setting owner references would: (a) prevent a provider from being in multiple groups (only one controller owner allowed), (b) delete providers when a group is deleted, which is not the intended semantics.
**Instead:** MCPProviderGroup is a read-only aggregation layer. It watches MCPProviders but does not own them. The relationship is selector-based, like how a Service selects Pods.

### Anti-Pattern 2: Discovery Controller Modifying MCPProviders It Did Not Create

**What:** MCPDiscoverySource in Authoritative mode deleting MCPProviders that exist but were not created by this discovery source.
**Why bad:** Would delete manually-created or other-discovery-source MCPProviders, causing data loss.
**Instead:** Filter authoritative deletes to only MCPProviders with the label `mcp-hangar.io/discovery-source: <source-name>`. Never touch MCPProviders without this label.

### Anti-Pattern 3: Shared Mutable State Between Controllers

**What:** Using package-level variables or shared maps between the three controllers.
**Why bad:** Controllers run concurrently. Shared state needs locking, complicates testing, creates coupling.
**Instead:** Each controller is fully independent. Cross-resource communication happens through the Kubernetes API (status subresources, labels, owner references). The existing `pkg/metrics` global vars are safe because Prometheus registries are concurrency-safe by design.

### Anti-Pattern 4: Deep Reconciliation Nesting

**What:** MCPDiscoverySource reconciler directly invoking MCPProvider reconciler.
**Why bad:** Bypasses the controller-runtime work queue, rate limiting, and leader election.
**Instead:** MCPDiscoverySource creates MCPProvider CRs via the API. The controller-runtime informer cache detects the new resource and enqueues it for the MCPProvider controller. This is the standard "level-triggered" reconciliation model.

## Data Flow

### MCPProviderGroup Aggregation Flow

```
MCPProviderGroup reconcile triggered
    |
    v
List MCPProviders matching spec.selector
    |
    v
For each matched MCPProvider:
    - Read status.state
    - Read status.tools
    - Read status.lastHealthCheck
    |
    v
Aggregate into MCPProviderGroupStatus:
    - providerCount = len(matched)
    - readyCount = count(state == Ready)
    - degradedCount = count(state == Degraded)
    - coldCount = count(state == Cold)
    - deadCount = count(state == Dead)
    - providers[] = member details
    |
    v
Evaluate HealthPolicy:
    - IsHealthy(policy) -> set Ready/Degraded condition
    |
    v
Update MCPProviderGroupStatus
    |
    v
Optional: Sync to HangarClient (register group, update strategy)
```

### MCPDiscoverySource Provider Creation Flow

```
MCPDiscoverySource reconcile triggered
    |
    v
Check if paused -> skip if paused
    |
    v
Select strategy based on spec.type:
    Namespace   -> scan namespaces for annotated resources
    ConfigMap   -> parse providers.yaml from referenced ConfigMap
    Annotations -> scan Pods/Services with MCP annotations
    ServiceDiscovery -> scan Services with matching selector
    |
    v
Apply filters (includePatterns, excludePatterns, maxProviders)
    |
    v
For each discovered provider:
    |
    v
    Check if MCPProvider CR exists (by name + discovery-source label)
    |
    +--> Does not exist: Create MCPProvider CR
    |        - Apply providerTemplate defaults
    |        - Set owner reference to MCPDiscoverySource
    |        - Set label mcp-hangar.io/discovery-source: <name>
    |
    +--> Already exists: Update if spec changed
    |
    v
If Authoritative mode:
    - List MCPProviders with discovery-source label
    - Delete any not in discovered set
    |
    v
Update MCPDiscoverySourceStatus:
    - discoveredCount, managedCount
    - lastSyncTime, lastSyncDuration
    - discoveredProviders[]
    |
    v
RequeueAfter: refreshInterval
```

### Cross-Controller Watch Flow

```
MCPProvider status changes (e.g., Cold -> Ready)
    |
    v
controller-runtime informer detects change
    |
    +--> MCPProvider controller: reconciles own status (existing)
    |
    +--> MCPProviderGroup controller: mapProviderToGroups()
             |
             v
         List all MCPProviderGroups in namespace
         For each group, check if selector matches provider labels
         If match: enqueue group for reconciliation
             |
             v
         Group re-aggregates all member statuses
```

## Integration Points

### New Components

| Component | File | Purpose |
|-----------|------|---------|
| MCPProviderGroupReconciler | `internal/controller/mcpprovidergroup_controller.go` | Group reconciliation with label selection |
| MCPDiscoverySourceReconciler | `internal/controller/mcpdiscoverysource_controller.go` | Discovery reconciliation with provider creation |
| DiscoveryStrategy interface | `pkg/discovery/interface.go` | Common interface for discovery types |
| NamespaceDiscovery | `pkg/discovery/namespace.go` | Namespace-scanning strategy |
| ConfigMapDiscovery | `pkg/discovery/configmap.go` | ConfigMap-based strategy |
| AnnotationDiscovery | `pkg/discovery/annotations.go` | Annotation-based strategy |
| ServiceDiscovery | `pkg/discovery/service.go` | Service-based strategy |

### Modified Components

| Component | File | Change |
|-----------|------|--------|
| main.go | `cmd/operator/main.go` | Add MCPProviderGroup + MCPDiscoverySource controller setup |
| HangarClient | `pkg/hangar/client.go` | Optional: add RegisterGroup/SyncGroup methods |
| mkdocs.yml | `mkdocs.yml` | Add 4 new reference/guide pages to nav |

### Unchanged Components

| Component | Reason |
|-----------|--------|
| MCPProvider controller | Discovery-created MCPProviders are standard CRs; no special handling needed |
| Pod builder | Only used by MCPProvider controller for container mode |
| Metrics | Already pre-defined for all 3 controllers; just need to call them |
| CRD types | All 3 already fully defined with helpers and deepcopy |
| RBAC (Helm) | Already configured for all 3 CRDs |

### Documentation Integration Points

| New Page | Nav Location | Plugin Usage |
|----------|-------------|-------------|
| `reference/configuration.md` | Reference section | Pure markdown, YAML code blocks |
| `reference/tools.md` | Reference section | mkdocstrings for Python docstrings from `server/tools/` |
| `guides/PROVIDER_GROUPS.md` | Guides section | Pure markdown, CRD YAML examples |
| `guides/FACADE_API.md` | Guides section | mkdocstrings for Hangar/SyncHangar classes |

**mkdocstrings integration for tools reference:**

```markdown
<!-- reference/tools.md -->
::: mcp_hangar.server.tools.hangar
    options:
      show_source: false
      members:
        - hangar_status
        - list_providers
        ...
```

This leverages the already-configured mkdocstrings Python handler in mkdocs.yml (line 46-49). The handler is installed but unused -- v0.10 will be its first real usage.

## Build Order (Dependency-Driven)

### Phase 1: MCPProviderGroup Controller

**Rationale:** No external dependencies. Reads MCPProvider status (which already exists) and writes to its own status. Pure aggregation logic with well-defined inputs/outputs.

1. Write `mcpprovidergroup_controller.go` with:
   - `SetupWithManager` using `Watches` + `EnqueueRequestsFromMapFunc`
   - `Reconcile` with label selector matching
   - Status aggregation logic
   - Health policy evaluation
   - Metrics emission (using pre-defined `GroupProviderCount`)
2. Register in `main.go`
3. Write tests (unit for aggregation, integration for cross-resource watch)

### Phase 2: MCPDiscoverySource Controller

**Rationale:** Depends on MCPProvider types being reconcilable (already true). More complex than Group because it creates resources. Discovery strategies can be built incrementally.

1. Define `pkg/discovery/` interface and implement strategies
2. Write `mcpdiscoverysource_controller.go` with:
   - Strategy selection based on `spec.type`
   - MCPProvider CR creation with owner references
   - Additive vs Authoritative mode handling
   - Status tracking (discovered, managed counts)
   - Periodic rescan via RequeueAfter
3. Register in `main.go`
4. Write tests (unit per strategy, integration for create/delete lifecycle)

### Phase 3: Documentation Pages

**Rationale:** Independent of controllers. Can be done in parallel with Phase 1-2 but listed last because it has no code dependencies.

1. Create `reference/configuration.md` and `reference/tools.md`
2. Create `guides/PROVIDER_GROUPS.md` and `guides/FACADE_API.md`
3. Update `mkdocs.yml` nav section
4. Validate mkdocstrings integration works for Python handler

## Scalability Considerations

| Concern | At 10 providers | At 100 providers | At 1000 providers |
|---------|-----------------|-------------------|---------------------|
| Group aggregation | Trivial: list + count | Fine: single List call with selector | Use field selectors or cached informers to reduce load |
| Discovery scan | Fast: few resources | Moderate: batch List calls | Use label selectors to narrow scan scope; increase refreshInterval |
| Cross-resource watch volume | Low: few enqueues | Moderate: each provider change may trigger multiple group reconciles | Rate-limit group reconciles via MaxConcurrentReconciles config |
| MCPProvider CR creation | Instant | Batch in reconcile loop | Consider server-side apply for idempotent creates; add MaxProviders filter |

The existing `MaxConcurrentReconciles: 10` config in `ReconcilerConfig` should be applied to all three controllers. At scale, the Group controller's `mapProviderToGroups` function is the hottest path -- it lists all groups per provider change. For clusters with many groups, add a label index to speed up the selector match.

## Sources

- Existing codebase analysis (all files read above) -- HIGH confidence
- controller-runtime Watches/MapFunc pattern -- HIGH confidence (established pattern since controller-runtime v0.6+, documented in Kubebuilder book)
- Owner reference garbage collection -- HIGH confidence (core Kubernetes API server behavior)
- mkdocstrings Python handler -- HIGH confidence (plugin already configured in mkdocs.yml)
