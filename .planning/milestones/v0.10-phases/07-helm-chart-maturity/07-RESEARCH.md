# Phase 7: Helm Chart Maturity - Research

**Researched:** 2026-03-01
**Domain:** Helm chart versioning, NOTES.txt post-install guidance, Helm test templates
**Confidence:** HIGH

## Summary

Phase 7 is a chart maturity phase with three focused deliverables: version bumping both charts to 0.10.0, adding NOTES.txt files with post-install instructions, and adding Helm test templates for installation validation. The existing charts already pass `helm lint` at their current 0.2.0 version, have well-structured templates with standard `_helpers.tpl` helpers, and follow Helm apiVersion v2 conventions. No new Kubernetes resources, values, or chart features are needed.

The work is entirely additive -- new files only (NOTES.txt, test templates) plus version field edits in Chart.yaml. Both charts have established patterns (labels, service names, health endpoints) that test templates can reference directly. The user has locked NOTES.txt content decisions: static text only (no Go template conditionals), with specific content requirements for each chart.

**Primary recommendation:** Create one plan covering all three requirements across both charts, since the work is straightforward, tightly coupled, and can be completed in a single pass.

<user_constraints>

## User Constraints (from CONTEXT.md)

### Locked Decisions

- NOTES.txt Content -- Server Chart: Operational essentials only: endpoint URL with kubectl port-forward command, health check URL, how to view logs. Include documentation links: site URL plus direct links to Configuration Reference and Provider Groups Guide pages. Static text -- no Go template conditionals based on values.
- NOTES.txt Content -- Operator Chart: CRD status commands: kubectl get mcpproviders, mcpprovidergroups, mcpdiscoverysources. CRD lifecycle note: CRDs are not removed on helm uninstall, with manual removal command. Include same documentation links as server chart. Static text -- no Go template conditionals based on values.

### Claude's Discretion

- Version bump strategy: how to bump Chart.yaml version/appVersion from 0.2.0 to 0.10.0 (whether to also add kubeVersion constraint to server chart)
- Helm test template scope: what validations to run (pod readiness, endpoint connectivity, CRD existence, health probes)
- Chart alignment: whether to harmonize server chart templates with operator chart patterns (podLabels, PDB, priorityClass, topologySpread) or keep them as-is
- Test template naming and structure within templates/tests/ directories
- Exact wording and formatting of NOTES.txt output

### Deferred Ideas (OUT OF SCOPE)

None -- discussion stayed within phase scope.

</user_constraints>

<phase_requirements>

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| HELM-01 | Both charts (mcp-hangar, mcp-hangar-operator) updated to version 0.10.0 | Version bump in Chart.yaml (version + appVersion fields); add kubeVersion constraint to server chart for parity |
| HELM-02 | Both charts include NOTES.txt with post-install instructions (endpoints, status commands, docs links) | Static NOTES.txt files in templates/ directory; content locked in CONTEXT.md |
| HELM-03 | Both charts include Helm test templates for installation validation | Pod-based tests in templates/tests/ using helm.sh/hook: test annotation; test connectivity and health endpoints |

</phase_requirements>

## Standard Stack

### Core

| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| Helm | v4.1.1 (installed locally) | Chart packaging, linting, testing | Project's deployment mechanism |
| Helm Chart API | v2 | Chart.yaml schema | Both charts already use apiVersion: v2 |

### Supporting

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `helm lint` | Validate chart structure | After every change, before commit |
| `helm template` | Render templates locally | Verify NOTES.txt and test templates render correctly |
| `helm test` | Run test pods in cluster | After installation to validate deployment |

### Alternatives Considered

None. This phase adds files to existing Helm charts -- no library or tool choices needed.

**Installation:** No new tools required. Helm v4.1.1 already available locally.

## Architecture Patterns

### File Structure (What to Add)

```
packages/helm-charts/mcp-hangar/
├── Chart.yaml              # UPDATE: version 0.10.0, appVersion 0.10.0, add kubeVersion
└── templates/
    ├── NOTES.txt            # NEW: post-install guidance
    └── tests/
        └── test-connection.yaml   # NEW: connectivity test

packages/helm-charts/mcp-hangar-operator/
├── Chart.yaml              # UPDATE: version 0.10.0, appVersion 0.10.0
└── templates/
    ├── NOTES.txt            # NEW: post-install guidance
    └── tests/
        ├── test-health.yaml       # NEW: health endpoint test
        └── test-crds.yaml         # NEW: CRD existence test
```

### Pattern 1: NOTES.txt as Static Text

**What:** NOTES.txt files that display fixed post-install instructions without conditional logic based on values.
**When to use:** When chart configuration is predictable and users benefit from consistent, always-visible guidance.
**Example (Server Chart):**

```
MCP Hangar has been installed.

Get the application URL:
  kubectl port-forward --namespace {{ .Release.Namespace }} svc/{{ include "mcp-hangar.fullname" . }} 8080:{{ .Values.service.port }}

Check health:
  curl http://127.0.0.1:8080/health

View logs:
  kubectl logs --namespace {{ .Release.Namespace }} -l app.kubernetes.io/instance={{ .Release.Name }} -f

Documentation:
  https://mcp-hangar.io
  https://mcp-hangar.io/reference/configuration/
  https://mcp-hangar.io/guides/PROVIDER_GROUPS/
```

**Key insight:** While NOTES.txt supports Go templates, the user decision locks this to static text. The only templates used are `.Release.Namespace`, `.Release.Name`, and helper includes for service names -- these are structural references, not conditional logic based on values.

### Pattern 2: Helm Test as Pod with hook annotation

**What:** A Pod resource annotated with `helm.sh/hook: test` that runs a command and exits 0 on success.
**When to use:** Standard Helm test pattern for post-install validation.
**Source:** https://helm.sh/docs/topics/chart_tests/

**Example:**

```yaml
# Source: Helm official documentation
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "mcp-hangar.fullname" . }}-test-connection"
  labels:
    {{- include "mcp-hangar.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
spec:
  containers:
    - name: wget
      image: busybox
      command: ['wget']
      args: ['{{ include "mcp-hangar.fullname" . }}:{{ .Values.service.port }}']
  restartPolicy: Never
```

### Pattern 3: CRD Existence Test

**What:** A test pod that uses `kubectl` to verify CRDs exist in the cluster.
**When to use:** For operator charts that install CRDs.
**Example:**

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "mcp-hangar-operator.fullname" . }}-test-crds"
  labels:
    {{- include "mcp-hangar-operator.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
spec:
  serviceAccountName: {{ include "mcp-hangar-operator.serviceAccountName" . }}
  containers:
    - name: check-crds
      image: bitnami/kubectl:latest
      command:
        - sh
        - -c
        - |
          kubectl get crd mcpproviders.mcp-hangar.io && \
          kubectl get crd mcpprovidergroups.mcp-hangar.io && \
          kubectl get crd mcpdiscoverysources.mcp-hangar.io
  restartPolicy: Never
```

### Anti-Patterns to Avoid

- **Conditional NOTES.txt:** User explicitly decided against Go template conditionals based on values. Do not add `{{ if .Values.ingress.enabled }}` blocks.
- **Heavy test images:** Do not use full application images for tests; use lightweight busybox/wget or bitnami/kubectl.
- **Tests with namespace hardcoded:** Use `.Release.Namespace` template variable, never hardcode namespace.
- **Tests that modify state:** Helm tests should be read-only validation, not setup or mutation operations.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP connectivity test | Custom curl container | busybox `wget` | Standard Helm test pattern, lightweight (~1MB image) |
| CRD validation | Custom Go binary | bitnami/kubectl image | Pre-built, maintained, has kubectl binary |
| Template rendering verification | Manual YAML inspection | `helm template` + `helm lint` | Built into Helm, catches syntax errors |

**Key insight:** Helm test infrastructure is intentionally simple -- Pods with exit codes. Do not overcomplicate with custom images or frameworks.

## Common Pitfalls

### Pitfall 1: NOTES.txt Not Rendered by helm lint

**What goes wrong:** NOTES.txt is a template file processed by Go templating. Syntax errors in NOTES.txt will cause `helm install` to fail but `helm lint` catches them.
**Why it happens:** NOTES.txt is treated as a template, not plain text.
**How to avoid:** Run `helm template` after creating NOTES.txt to verify it renders. Even "static" NOTES.txt that uses `.Release.Namespace` is a template.
**Warning signs:** `helm template` output shows error or no NOTES section.

### Pitfall 2: Test Pod Service Account Permissions

**What goes wrong:** CRD existence test pod needs `get` permission on CRDs, but the test pod's service account may lack RBAC for `apiextensions.k8s.io` resources.
**Why it happens:** The operator's ClusterRole grants permissions for mcp-hangar.io resources, not necessarily for reading CRD definitions themselves.
**How to avoid:** Either use a simple connectivity test that does not need special RBAC (wget to health endpoint), or ensure the test pod uses a service account with CRD read access. The operator's existing ClusterRole already has broad permissions; verify it includes `apiextensions.k8s.io` CRD `get`.
**Recommendation:** For the operator chart, prefer a health endpoint connectivity test (wget to the health port) over a CRD existence test. CRD existence is implicitly validated -- if CRDs are missing, the operator itself will fail health checks. This avoids RBAC complications entirely.

### Pitfall 3: Version and appVersion Confusion

**What goes wrong:** `version` is the chart version (for Helm repository), `appVersion` is the application version. They can diverge but this project keeps them synchronized.
**Why it happens:** SemVer applies to `version`; `appVersion` is free-form.
**How to avoid:** Update both `version` and `appVersion` to `0.10.0` in both Chart.yaml files. Keep them synchronized as the project convention.
**Warning signs:** `helm lint` will warn if version is not valid SemVer.

### Pitfall 4: Test Pod Image Tag

**What goes wrong:** Using `latest` tag for test images (busybox, bitnami/kubectl) can cause unexpected failures when images change.
**Why it happens:** Image tag best practices are often skipped for test-only pods.
**How to avoid:** Pin busybox to a specific version (e.g., `busybox:1.37`) for reproducibility. For kubectl images, pin to a Kubernetes-compatible version.
**Recommendation:** Use `busybox:1.37` for wget-based tests. Avoid kubectl-based tests entirely (see Pitfall 2).

### Pitfall 5: helm test Cleanup

**What goes wrong:** Test pods remain in the namespace after `helm test` completes.
**Why it happens:** Helm v3+ does not auto-delete test pods by default.
**How to avoid:** Add `helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded` annotation to auto-clean successful test pods. Or document `helm test --cleanup` (Helm 4 removed `--cleanup`; use `kubectl delete pod` or add the annotation).
**Recommendation:** Add `"helm.sh/hook-delete-policy": "before-hook-creation,hook-succeeded"` annotation to all test pods for clean test reruns.

## Code Examples

### Server Chart: Chart.yaml Version Bump

```yaml
# packages/helm-charts/mcp-hangar/Chart.yaml
apiVersion: v2
name: mcp-hangar
description: Production-grade infrastructure for Model Context Protocol
type: application
version: 0.10.0
appVersion: "0.10.0"
kubeVersion: ">=1.25.0-0"
home: https://mcp-hangar.io
sources:
  - https://github.com/mcp-hangar/mcp-hangar
maintainers:
  - name: mcp-hangar
    email: marcin@mcp-hangar.io
keywords:
  - mcp
  - model-context-protocol
  - ai
  - llm
```

### Server Chart: NOTES.txt

```
# packages/helm-charts/mcp-hangar/templates/NOTES.txt
MCP Hangar has been installed.

Get the application URL:

  kubectl port-forward --namespace {{ .Release.Namespace }} svc/{{ include "mcp-hangar.fullname" . }} 8080:{{ .Values.service.port }}

Check health:

  curl http://127.0.0.1:8080/health

View logs:

  kubectl logs --namespace {{ .Release.Namespace }} -l app.kubernetes.io/instance={{ .Release.Name }} -f

Documentation:

  https://mcp-hangar.io
  Configuration Reference: https://mcp-hangar.io/reference/configuration/
  Provider Groups Guide:   https://mcp-hangar.io/guides/PROVIDER_GROUPS/
```

### Server Chart: Test Connection Template

```yaml
# packages/helm-charts/mcp-hangar/templates/tests/test-connection.yaml
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "mcp-hangar.fullname" . }}-test-connection"
  labels:
    {{- include "mcp-hangar.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  containers:
    - name: wget
      image: busybox:1.37
      command: ['wget']
      args: ['--spider', '--timeout=5', '{{ include "mcp-hangar.fullname" . }}:{{ .Values.service.port }}/health']
  restartPolicy: Never
```

### Operator Chart: NOTES.txt

```
# packages/helm-charts/mcp-hangar-operator/templates/NOTES.txt
MCP Hangar Operator has been installed.

Check CRD status:

  kubectl get mcpproviders
  kubectl get mcpprovidergroups
  kubectl get mcpdiscoverysources

View operator logs:

  kubectl logs --namespace {{ .Release.Namespace }} -l app.kubernetes.io/instance={{ .Release.Name }} -f

NOTE: CRDs are not removed when you run `helm uninstall`.
To remove CRDs manually:

  kubectl delete crd mcpproviders.mcp-hangar.io
  kubectl delete crd mcpprovidergroups.mcp-hangar.io
  kubectl delete crd mcpdiscoverysources.mcp-hangar.io

Documentation:

  https://mcp-hangar.io
  Configuration Reference: https://mcp-hangar.io/reference/configuration/
  Provider Groups Guide:   https://mcp-hangar.io/guides/PROVIDER_GROUPS/
```

### Operator Chart: Test Health Template

```yaml
# packages/helm-charts/mcp-hangar-operator/templates/tests/test-health.yaml
apiVersion: v1
kind: Pod
metadata:
  name: "{{ include "mcp-hangar-operator.fullname" . }}-test-health"
  labels:
    {{- include "mcp-hangar-operator.labels" . | nindent 4 }}
  annotations:
    "helm.sh/hook": test
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  containers:
    - name: wget
      image: busybox:1.37
      command: ['wget']
      args: ['--spider', '--timeout=5', '{{ include "mcp-hangar-operator.fullname" . }}-metrics:{{ .Values.operator.metrics.port }}']
  restartPolicy: Never
```

## Discretion Recommendations

### Version Bump Strategy

**Recommendation:** Update both `version` and `appVersion` to `0.10.0` in both Chart.yaml files. Add `kubeVersion: ">=1.25.0-0"` to the server chart for parity with the operator chart. Kubernetes 1.25 is a reasonable minimum (stable CRDs, PodSecurity admission). This is a simple field edit, not a behavioral change.

### Helm Test Template Scope

**Recommendation:** Keep tests minimal and RBAC-free:

- **Server chart:** One test -- `test-connection.yaml` -- wget to service health endpoint (`/health` on port 8080). This validates the deployment is running, the service is routing, and the health endpoint responds.
- **Operator chart:** One test -- `test-health.yaml` -- wget to the metrics service endpoint. This validates the operator deployment is running and the metrics service is routing correctly.

**Rationale against CRD existence tests:** CRD tests require kubectl images (larger, slower) and RBAC for `apiextensions.k8s.io` resources (the operator's ClusterRole does not include this). CRD existence is implicitly validated -- if CRDs are missing, the operator will crash-loop and fail the health test anyway. The health connectivity test covers the same ground with zero RBAC overhead.

### Chart Alignment

**Recommendation:** Do NOT harmonize server chart templates with operator chart patterns (podLabels, PDB, priorityClass, topologySpread) in this phase. The user's CONTEXT.md explicitly states: "This phase does not add new chart features, templates, or values -- it matures existing charts with versioning, user guidance, and test coverage." Chart template harmonization is out of scope.

### Test Template Naming

**Recommendation:** Follow Helm conventions:

- Server: `templates/tests/test-connection.yaml` (matches `helm create` default naming)
- Operator: `templates/tests/test-health.yaml` (describes what it tests)

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `helm.sh/hook: test-success` | `helm.sh/hook: test` | Helm v3 | Old annotation still accepted but deprecated |
| No hook-delete-policy | `hook-delete-policy: before-hook-creation,hook-succeeded` | Best practice | Prevents stale test pods accumulating |
| `helm test --cleanup` | Removed in Helm 4 | Helm v4.0.0 | Use hook-delete-policy annotation instead |

**Deprecated/outdated:**

- `test-success` / `test-failure` hook annotations: Use `test` instead
- `helm test --cleanup` flag: Removed in Helm v4; use `hook-delete-policy` annotation

## Open Questions

None. All decisions are either locked by the user or have clear recommendations for the discretion areas.

## Sources

### Primary (HIGH confidence)

- Helm official docs: Chart Tests - https://helm.sh/docs/topics/chart_tests/ - test pod pattern, annotations, templates/tests/ convention
- Helm official docs: Creating NOTES.txt - https://helm.sh/docs/chart_template_guide/notes_files/ - NOTES.txt placement and templating
- Helm official docs: General Conventions - https://helm.sh/docs/chart_best_practices/conventions/ - SemVer versioning, YAML formatting
- Existing chart source code (Chart.yaml, _helpers.tpl, deployment.yaml, service.yaml for both charts) - verified all helper names, port names, service names, label patterns
- Local Helm v4.1.1 - verified `helm lint` passes for both charts at current state, verified `helm create` default test template

### Secondary (MEDIUM confidence)

- Bitnami nginx chart NOTES.txt (GitHub raw) - reference for complex conditional NOTES.txt (not needed for this phase but confirmed static approach is simpler)

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH - Helm is the only tool, well-documented, locally verified
- Architecture: HIGH - file locations and patterns are standard Helm conventions, verified with official docs and `helm create`
- Pitfalls: HIGH - verified Helm v4 deprecations (--cleanup removed), hook-delete-policy behavior, RBAC requirements for CRD tests

**Research date:** 2026-03-01
**Valid until:** 2026-06-01 (Helm chart conventions are stable; Helm v4 just released)
