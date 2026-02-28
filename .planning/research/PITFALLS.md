# Domain Pitfalls

**Domain:** Kubernetes multi-controller operator + documentation for MCP provider lifecycle platform
**Researched:** 2026-02-28
**Applies to:** v0.10 milestone -- MCPProviderGroup controller, MCPDiscoverySource controller, documentation reference pages, Helm chart sync

---

## Critical Pitfalls

Mistakes that cause production incidents, data loss, or require architectural rewrites.

### Pitfall 1: MCPDiscoverySource Creates MCPProviders That MCPProviderGroup Also Watches -- Infinite Reconciliation Loop

**What goes wrong:** MCPDiscoverySource controller creates MCPProvider resources. MCPProviderGroup controller watches MCPProviders matching a label selector. When a discovered provider is created/updated, it triggers MCPProviderGroup reconciliation. MCPProviderGroup status update triggers another MCPProviderGroup reconciliation (if not careful with status vs. spec changes). Meanwhile, MCPDiscoverySource's periodic refresh re-lists providers and may update labels, triggering the whole cycle again. The result is a tight reconciliation storm consuming API server resources and causing rate limiting.

**Why it happens:** The existing MCPProvider controller uses a simple `Owns(&corev1.Pod{})` watch (line 533 of mcpprovider_controller.go). MCPProviderGroup needs to watch MCPProviders via label selector, not ownership. Discovery creates MCPProviders and may set controller owner references (MCPDiscoverySource `ShouldSetController()` defaults to true). If MCPProviderGroup ALSO tries to set controller owner reference on the same MCPProvider, the API server rejects it (only one controller owner reference per object). Reconciliation retries indefinitely.

**Consequences:**

- API server throttled with excessive LIST/WATCH/UPDATE calls
- Operator pod OOMKilled from accumulated reconciliation queue
- Provider state oscillates as controllers fight over status
- Prometheus metrics cardinality explosion from rapid state changes

**Prevention:**

1. MCPProviderGroup must NOT set controller owner references on MCPProviders. It is a reader/aggregator, not an owner. Only MCPDiscoverySource or the user should own MCPProviders.
2. Use `handler.EnqueueRequestsFromMapFunc` for MCPProviderGroup to watch MCPProvider changes, mapping them to the group(s) whose label selector matches. Do NOT use `Owns()`.
3. MCPProviderGroup reconciler must compare computed status against current status and skip the update if nothing changed. Use `reflect.DeepEqual` or a purpose-built diff on the status struct.
4. MCPDiscoverySource must use `controllerutil.SetControllerReference` (not `SetOwnerReference`) only when `ShouldSetController()` is true, and must check if another controller reference already exists before attempting to set one.
5. Add a generation-based check: only requeue if `observedGeneration != generation` for spec changes; status-only changes should not trigger full reconciliation.

**Detection:**

- `mcp_operator_reconcile_total` counter increasing at >10/second for a single controller
- Operator logs showing repeated "Reconciling MCPProviderGroup" entries with <1s intervals
- `kubectl get events --field-selector reason=TooManyRequests`
- API server audit logs showing throttled requests from operator service account

---

### Pitfall 2: Finalizer Deadlock Between MCPDiscoverySource and Owned MCPProviders

**What goes wrong:** MCPDiscoverySource creates MCPProviders with controller owner reference and sets `blockDeletion: true` in its OwnershipConfig. When the MCPDiscoverySource is deleted, Kubernetes garbage collector tries to delete the owned MCPProviders. Each MCPProvider has the existing `mcp-hangar.io/finalizer` (line 28 of mcpprovider_controller.go) which requires cleanup (deregister from Hangar core, delete pod). If the Hangar core is down or the MCPProvider cleanup fails, the MCPProvider finalizer blocks deletion. This blocks MCPDiscoverySource deletion (because its children still exist). The MCPDiscoverySource's own finalizer cannot complete because it's waiting for children to be deleted. Circular dependency: parent waits for children, children's finalizers need a service that may be unavailable.

**Why it happens:** The existing MCPProvider controller's `reconcileDelete` (line 482-527) deregisters from Hangar core and continues anyway (`// Continue anyway - don't block deletion`). This is resilient. But the MCPDiscoverySource controller, if it adds its OWN finalizer and tries to clean up children in its finalizer, creates a layered dependency. The real danger is when MCPDiscoverySource's finalizer tries to explicitly delete MCPProviders (rather than relying on garbage collection), and those MCPProviders have their own finalizers still running.

**Consequences:**

- MCPDiscoverySource stuck in `Terminating` state indefinitely
- Namespace deletion blocked (if namespace has a terminating MCPDiscoverySource)
- Operator admin must manually remove finalizers via `kubectl edit`, which skips cleanup
- Cascading namespace deletion failures in CI/CD pipelines

**Prevention:**

1. MCPDiscoverySource's finalizer should NOT explicitly delete owned MCPProviders. Let Kubernetes garbage collection (via owner references with `blockOwnerDeletion: false` by default) handle cascade deletion.
2. For Authoritative mode: when MCPDiscoverySource is being deleted, remove the owner reference from managed MCPProviders first (orphaning them), then remove the finalizer. This lets providers survive source deletion if desired.
3. MCPDiscoverySource finalizer timeout: if cleanup takes >60s, log a warning and remove the finalizer anyway. Do not block indefinitely.
4. Set `blockDeletion: false` in the OwnershipConfig default (it already defaults to false in the types). Ensure the controller respects this.
5. Test the deletion path explicitly: create MCPDiscoverySource -> let it create MCPProviders -> delete MCPDiscoverySource -> verify all resources cleaned up within 30s.

**Detection:**

- `kubectl get mcpdiscoverysources -A` showing resources with `DeletionTimestamp` set but not removed
- `kubectl get mcpproviders -A` showing orphaned providers with owner reference pointing to deleted MCPDiscoverySource
- Operator logs showing repeated "Handling deletion for MCPDiscoverySource" without completion

---

### Pitfall 3: CRD Field Addition Breaks Existing Clusters on Helm Upgrade

**What goes wrong:** The Helm chart version jumps from 0.2.0 to 0.10.0. CRD YAMLs in `crds/` directory are replaced entirely. If new required fields are added to CRD schemas (even with defaults via kubebuilder), existing custom resources in the cluster may fail validation against the new CRD schema. Worse: `helm upgrade` applies CRDs BEFORE the new operator is running, so the old operator binary cannot handle new fields, and the new operator is not yet deployed to apply defaults.

**Why it happens:** Helm installs CRDs from the `crds/` directory during `helm install` only, NOT during `helm upgrade` (Helm's documented behavior). This means operators must either: (a) use a separate CRD installation step, (b) use a pre-upgrade hook, or (c) manage CRDs outside Helm entirely. The existing chart has `crds.install: true` and `crds.keep: true` in values.yaml, but no CRD upgrade mechanism visible in the templates. The 0.2.0->0.10.0 jump means the CRDs in the chart may have significant schema additions (MCPProviderGroup and MCPDiscoverySource CRDs exist but may need field changes).

**Consequences:**

- `helm upgrade` silently does NOT update CRDs -- new fields missing from cluster
- Operator crashes because it expects fields that don't exist in the CRD schema
- Users manually apply CRDs, version mismatch between CRD and operator binary
- Rollback is dangerous if CRD changes are not backward compatible

**Prevention:**

1. Add CRD upgrade documentation: explicit `kubectl apply -f crds/` step before `helm upgrade`.
2. Add a pre-upgrade hook Job that applies CRDs (common pattern). Template: `templates/crds-upgrade-job.yaml` with `helm.sh/hook: pre-upgrade`.
3. All new CRD fields MUST have defaults via `+kubebuilder:default=` markers. No new required fields without defaults.
4. Version the CRDs: add annotation `mcp-hangar.io/crd-version: "0.10.0"` so operators can detect version mismatch.
5. Test upgrade path: deploy v0.2.0, create resources, upgrade to v0.10.0, verify no validation errors.
6. NOTES.txt must include CRD upgrade instructions for `helm upgrade` users.

**Detection:**

- Operator pod in CrashLoopBackOff after upgrade with "unknown field" or "missing field" errors
- `kubectl get crd mcpproviders.mcp-hangar.io -o jsonpath='{.metadata.annotations}'` showing old controller-gen version
- Helm release status shows "deployed" but operator is not functional

---

### Pitfall 4: Status Update Conflicts (409 Conflict) Between MCPProvider Controller and MCPProviderGroup Controller

**What goes wrong:** Both the MCPProvider controller and the MCPProviderGroup controller read MCPProvider status to make decisions. If MCPProviderGroup reads an MCPProvider, then MCPProvider controller updates the same MCPProvider's status (e.g., health check results), MCPProviderGroup's subsequent status update on the MCPProvider will fail with HTTP 409 Conflict because the resourceVersion has changed. The current MCPProvider controller does NOT use `client.MergeFrom` patch -- it uses `r.Status().Update(ctx, mcpProvider)` which requires matching resourceVersion.

**Why it happens:** Looking at mcpprovider_controller.go: `r.Status().Update(ctx, mcpProvider)` is used throughout (lines 171, 186, 238, 253, 266, 313, 433, 474). This is a full status replacement that requires the object's resourceVersion to match. If another controller or even the same controller's concurrent reconciliation modifies the object between Get and Update, the Update fails. With MCPProviderGroup also reading MCPProvider objects, the contention window widens.

**Consequences:**

- Reconciliation errors spike in metrics
- Status updates lost, requiring requeue and extra API server load
- MCPProviderGroup shows stale provider state counts
- Providers appear to flap between Ready/Degraded in MCPProviderGroup status

**Prevention:**

1. MCPProviderGroup controller should ONLY read MCPProvider status. It must never write to MCPProvider objects. MCPProviderGroup updates only its OWN status sub-resource.
2. For MCPProvider controller, consider migrating status updates to use `r.Status().Patch(ctx, mcpProvider, client.MergeFrom(original))` instead of full `Update`. This is more resilient to concurrent modifications. But this is a larger refactor -- for v0.10, at minimum ensure MCPProviderGroup does not write to MCPProvider.
3. MCPDiscoverySource controller creates MCPProviders but should not update their status after creation. Let the MCPProvider controller own all status updates.
4. Add a `RetryOnConflict` wrapper around status updates in the new controllers from day one.

**Detection:**

- `mcp_operator_reconcile_total{result="error"}` increasing
- Operator logs showing "the object has been modified; please apply your changes to the latest version" errors
- Provider state in MCPProviderGroup status lagging behind actual MCPProvider status

---

## Moderate Pitfalls

### Pitfall 5: MCPProviderGroup Label Selector Watch Is Expensive at Scale

**What goes wrong:** MCPProviderGroup uses `metav1.LabelSelector` to select MCPProviders. The controller must list all MCPProviders, convert each MCPProviderGroup's label selector, and determine which providers belong to which groups. With N groups and M providers, this is O(N*M) per reconciliation cycle. At scale (100+ groups, 1000+ providers), this creates significant API server load.

**Prevention:**

1. Use an informer cache (controller-runtime's client already caches). Ensure `List` calls go through the cached client, not direct API calls.
2. Implement provider-to-group index: when MCPProvider changes, use `handler.EnqueueRequestsFromMapFunc` to find matching MCPProviderGroups and only reconcile those. Do NOT reconcile ALL groups on every provider change.
3. Add `labels.SelectorFromSet` with the informer's `ByIndex` capability for O(1) lookups.
4. Set `MaxConcurrentReconciles` for MCPProviderGroup controller to a conservative value (3-5, not 10 like MCPProvider).

### Pitfall 6: Discovery Authoritative Mode Deletes User-Created MCPProviders

**What goes wrong:** In `Authoritative` mode, MCPDiscoverySource syncs the cluster to match the discovery source. If a user manually creates an MCPProvider that does not match the discovery source, the controller deletes it on the next sync cycle -- it considers it "no longer discovered."

**Prevention:**

1. Only delete MCPProviders that have the controller owner reference pointing to this MCPDiscoverySource. Never delete resources you don't own.
2. Add a `mcp-hangar.io/managed-by: discovery` label to all discovery-created MCPProviders. Only delete resources with this label in authoritative mode.
3. Log a warning (and emit a Kubernetes event) before deleting any provider. Include the provider name and the discovery source that's removing it.
4. Add a `dryRun` field to MCPDiscoverySourceSpec that reports what WOULD be deleted without actually deleting.

### Pitfall 7: Documentation References New Controllers That Don't Exist Yet

**What goes wrong:** The existing KUBERNETES.md guide (lines 180-278) already documents MCPProviderGroup and MCPDiscoverySource usage with YAML examples. Users try to apply these manifests, but the controllers don't exist yet -- resources are created in etcd but never reconciled. Status fields remain empty. Users think the system is broken.

**Prevention:**

1. Phase the work: implement controllers BEFORE updating documentation to say they work. Or add clear "Coming in v0.10" banners.
2. Add a validating admission webhook (or at least a condition) that detects when a resource exists but no controller is reconciling it. Set a condition like `Type: Stale, Reason: NoController`.
3. The existing KUBERNETES.md should have a note about which features are implemented vs. planned. Add a status table at the top.
4. Documentation PRs should be merged AFTER controller PRs, or in the same release.

### Pitfall 8: mkdocstrings Python Handler Configured But No Go Documentation

**What goes wrong:** The mkdocs.yml configures `mkdocstrings` with a Python handler (lines 46-50). The v0.10 milestone adds Kubernetes operator documentation, which is Go code. The Python handler cannot generate docs from Go source. If someone tries to add `::: mcp_hangar.operator.controller` style references, mkdocs fails silently or generates empty sections.

**Prevention:**

1. Do NOT use mkdocstrings for Go code. Document the operator API manually in markdown tables (as already done in KUBERNETES.md's API Reference section).
2. If auto-generation is desired for Go types, use a separate tool like `crd-ref-docs` or `gen-crd-api-reference-docs` and generate markdown, then include it.
3. The CRD YAML schemas themselves are the authoritative API reference. Generate docs from CRD OpenAPI schemas using tools like `kubectl explain mcpprovider.spec --recursive`.
4. Add a CI check that ensures mkdocs builds successfully (`mkdocs build --strict`) to catch broken references.

### Pitfall 9: Shared Finalizer Name Across Controllers

**What goes wrong:** The existing MCPProvider controller uses `finalizerName = "mcp-hangar.io/finalizer"` (line 28). If the new MCPProviderGroup and MCPDiscoverySource controllers reuse this same finalizer name, removing the finalizer in one controller's deletion path could prematurely unblock deletion for a resource that another controller hasn't finished cleaning up.

**Prevention:**

1. Use distinct finalizer names per controller:
   - MCPProvider: `mcp-hangar.io/provider-finalizer` (rename from generic `mcp-hangar.io/finalizer`)
   - MCPProviderGroup: `mcp-hangar.io/group-finalizer`
   - MCPDiscoverySource: `mcp-hangar.io/discovery-finalizer`
2. If renaming the existing finalizer is too risky for backward compatibility, keep the existing one and use new distinct names for new controllers.
3. Test: delete each resource type independently and verify only its controller's finalizer logic runs.

### Pitfall 10: Helm Chart Version 0.2.0 to 0.10.0 -- values.yaml Breaking Changes

**What goes wrong:** Users have `values.yaml` overrides from 0.2.0. The 0.10.0 chart restructures or renames values keys. `helm upgrade` applies old values to new templates, producing invalid manifests or missing configuration. Common example: renaming `operator.reconcile.maxConcurrentReconciles` to `operator.controllers.maxConcurrency` breaks existing deployments.

**Prevention:**

1. Do NOT rename existing values keys. Add new keys alongside old ones.
2. Use `_helpers.tpl` to provide backward-compatible value lookups: check new key first, fall back to old key.
3. Document all values changes in the chart's CHANGELOG and UPGRADE.md.
4. Add `helm template` tests that render templates with both old and new values structures and verify output.
5. Include a migration guide in NOTES.txt that prints on upgrade.

---

## Minor Pitfalls

### Pitfall 11: Metrics Label Cardinality from Discovery

**What goes wrong:** MCPDiscoverySource creates providers dynamically. Each provider adds labels to Prometheus metrics (`namespace`, `name`). In dynamic environments with frequent provider creation/deletion, metric cardinality grows unbounded, causing Prometheus OOM.

**Prevention:**

1. Clean up metrics when providers are deleted (the existing controller already does this -- lines 510-515).
2. Ensure new controllers follow the same pattern: delete metric label sets on resource deletion.
3. Add `MaxProviders` filter in MCPDiscoverySource to cap discovery (already in the types as `Filters.MaxProviders`). Document recommended limits.

### Pitfall 12: Documentation Cross-Reference Drift

**What goes wrong:** The mkdocs.yml nav references `security/AUTH_SECURITY_AUDIT.md` (line 100) but the file is at `docs/security/AUTH_SECURITY_AUDIT.md`. New reference pages added to the nav may have wrong paths, causing mkdocs build to succeed (with warnings) but produce broken links.

**Prevention:**

1. Run `mkdocs build --strict` in CI. This treats warnings as errors, catching broken nav references.
2. After adding new pages, run a link checker (e.g., `linkchecker` or `htmltest`) against the built site.
3. Maintain a simple test: for every entry in `mkdocs.yml` nav, assert the file exists at the expected path.

### Pitfall 13: repo_url Points to Wrong GitHub Organization

**What goes wrong:** The mkdocs.yml has `repo_url: https://github.com/mapyr/mcp-hangar` and `repo_name: mapyr/mcp-hangar` (lines 7-8). But the Chart.yaml, CRD annotations, and code imports reference `github.com/mcp-hangar/mcp-hangar`. The PROJECT.md mentions fixing "old org name references" as a v0.10 target. If only some files are updated, users following "Edit on GitHub" links from docs land on 404 pages.

**Prevention:**

1. Fix the org name in ALL files in a single commit, not spread across multiple PRs.
2. Use `grep -r "mapyr" --include="*.md" --include="*.yml" --include="*.yaml"` to find all references.
3. After fixing, add a CI check that greps for the old org name and fails if found.

### Pitfall 14: MCPProviderGroup Reconciler Stale Cache After Provider Label Change

**What goes wrong:** A user changes labels on an MCPProvider, removing it from one group's selector and adding it to another. The MCPProviderGroup reconciler uses the cached client. If the cache hasn't synced yet, the provider appears in both groups or neither group temporarily. The group status shows incorrect provider counts.

**Prevention:**

1. Accept eventual consistency -- this is inherent to controller-runtime's informer cache. Document that group membership changes may take up to 1 reconciliation cycle (configurable via requeue interval).
2. Use `EnqueueRequestsFromMapFunc` on MCPProvider label changes to immediately trigger reconciliation of affected groups.
3. In MCPProviderGroup status, include `lastReconcileTime` so users can see when the status was last computed.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation | Severity |
|---|---|---|---|
| MCPProviderGroup controller | Infinite reconciliation loop (Pitfall 1) | No-op detection on status, EnqueueRequestsFromMapFunc instead of Owns | Critical |
| MCPProviderGroup controller | Status update conflicts (Pitfall 4) | Group controller reads MCPProvider but never writes to it | Critical |
| MCPDiscoverySource controller | Finalizer deadlock (Pitfall 2) | Rely on GC, don't explicitly delete children in finalizer | Critical |
| MCPDiscoverySource controller | Authoritative mode deletes user resources (Pitfall 6) | Only delete owned resources with managed-by label | Moderate |
| MCPDiscoverySource controller | Owner reference conflict with MCPProviderGroup (Pitfall 1) | Only MCPDiscoverySource sets controller owner ref | Critical |
| Helm chart 0.2.0 to 0.10.0 | CRD not upgraded on helm upgrade (Pitfall 3) | Pre-upgrade hook or documented manual CRD apply | Critical |
| Helm chart 0.2.0 to 0.10.0 | values.yaml breaking changes (Pitfall 10) | Don't rename keys, add backward compat helpers | Moderate |
| Documentation reference pages | References features before controllers exist (Pitfall 7) | Merge docs after controller implementation | Moderate |
| Documentation reference pages | mkdocstrings can't document Go (Pitfall 8) | Manual markdown tables or CRD schema generator | Minor |
| Documentation fixes | Org name inconsistency (Pitfall 13) | Single commit for all org name changes | Minor |
| Cross-controller coordination | Shared finalizer name (Pitfall 9) | Distinct finalizer per controller type | Moderate |

---

## "Looks Done But Isn't" Checklist

These are conditions that pass basic testing but fail in production or edge cases.

- [ ] **MCPProviderGroup status shows correct counts** -- but did you test with providers being deleted mid-reconciliation? Race between List and individual Get can show stale data.
- [ ] **MCPDiscoverySource creates providers** -- but did you test what happens when the same provider is discovered by TWO different MCPDiscoverySources? Owner reference conflict.
- [ ] **Helm chart renders cleanly** -- but did you test `helm upgrade` from 0.2.0 with existing custom values? `helm template` is not sufficient; test the actual upgrade path.
- [ ] **CRDs install successfully** -- but did you test applying the new CRD schema over an existing CRD with existing custom resources? Validation schema changes can reject existing resources.
- [ ] **Documentation builds with mkdocs** -- but did you test with `--strict`? Warnings (broken links, missing files) are silent by default.
- [ ] **MCPProviderGroup watches MCPProviders** -- but did you test with 0 matching providers? Empty selector results should show healthy group with 0 providers, not error.
- [ ] **Discovery respects filters** -- but did you test regex patterns with special characters in provider names? Malformed regex crashes the controller.
- [ ] **Finalizer cleanup works** -- but did you test deletion when Hangar core is unreachable? The existing controller handles this (continues anyway), new controllers must do the same.
- [ ] **MCPProviderGroup failover works** -- but did you test failover when ALL providers in the group are degraded? The group should transition to Degraded, not loop retrying.
- [ ] **Documentation cross-references work** -- but did you test the "Edit on GitHub" links? They use `edit_uri` which combines `repo_url` + path, so org name must be correct.

---

## Technical Debt Patterns to Avoid

### Pattern 1: Copy-Paste Controller Boilerplate

**Trap:** Copying mcpprovider_controller.go wholesale for the new controllers and modifying it. The existing controller has patterns worth reusing (finalizer handling, status conditions, metrics) but also patterns that should NOT be copied (full status replacement instead of patch).

**Instead:** Extract shared utilities (condition setting, finalizer management, metrics recording) into the `pkg/` directory. Each controller should be lean, delegating common operations to shared packages.

### Pattern 2: Documentation as Afterthought

**Trap:** Implementing controllers first, then writing documentation that describes the implementation rather than the user's mental model. Result: docs that explain HOW it works internally but not WHY a user would use it or WHAT to do when it breaks.

**Instead:** Write the user-facing documentation first (Configuration Reference, Provider Groups Guide) as a specification. Then implement to match the docs. If the implementation diverges, update the docs in the same PR.

### Pattern 3: Monolithic Test Files

**Trap:** Adding all new controller tests to the existing `controller_test.go` (which currently has only 24 lines). As controllers grow, this becomes an unmaintainable test file.

**Instead:** One test file per controller: `mcpprovider_controller_test.go`, `mcpprovidergroup_controller_test.go`, `mcpdiscoverysource_controller_test.go`. Use envtest for integration tests, pure unit tests for business logic.

### Pattern 4: RBAC Permissions Pre-Granted

**Trap:** The ClusterRole already grants full permissions for all three CRDs (lines 12-45 of clusterrole.yaml). This is fine for the operator, but if MCPDiscoverySource creates MCPProviders in other namespaces, the ClusterRole grants cross-namespace write access. This is correct for the operator but surprising for cluster admins who expect namespace isolation.

**Instead:** Document the RBAC implications clearly. Consider offering a namespaced Role option for single-namespace deployments. Add `rbac.scope: cluster|namespace` to values.yaml.

---

## Sources

- controller-runtime documentation (HIGH confidence -- standard patterns for multi-controller operators)
- Existing MCPProvider controller source code at `packages/operator/internal/controller/mcpprovider_controller.go` (HIGH confidence -- direct code analysis)
- Existing CRD type definitions at `packages/operator/api/v1alpha1/` (HIGH confidence -- direct code analysis)
- Helm CRD lifecycle documentation: Helm does NOT upgrade CRDs from `crds/` directory (HIGH confidence -- documented Helm behavior)
- Existing `mkdocs.yml` configuration showing mkdocstrings Python handler and nav structure (HIGH confidence -- direct file analysis)
- Kubernetes owner reference semantics: only one controller owner reference per object (HIGH confidence -- Kubernetes API specification)
- Existing `values.yaml` and `clusterrole.yaml` for Helm chart patterns (HIGH confidence -- direct file analysis)
