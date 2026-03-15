<!-- markdownlint-disable MD024 -->

# Phase 7: Helm Chart Maturity - Context

**Gathered:** 2026-03-01
**Status:** Ready for planning

<domain>

## Phase Boundary

Both Helm charts (mcp-hangar server and mcp-hangar-operator) are version-synchronized to 0.10.0 and include post-install guidance (NOTES.txt) and automated test validation (helm test templates). This phase does not add new chart features, templates, or values -- it matures existing charts with versioning, user guidance, and test coverage.

</domain>

<decisions>

## Implementation Decisions

### NOTES.txt Content -- Server Chart

- Operational essentials only: endpoint URL with kubectl port-forward command, health check URL, how to view logs
- Include documentation links: site URL plus direct links to Configuration Reference and Provider Groups Guide pages
- Static text -- no Go template conditionals based on values

### NOTES.txt Content -- Operator Chart

- CRD status commands: kubectl get mcpproviders, mcpprovidergroups, mcpdiscoverysources
- CRD lifecycle note: CRDs are not removed on helm uninstall, with manual removal command
- Include same documentation links as server chart
- Static text -- no Go template conditionals based on values

### Claude's Discretion

- Version bump strategy: how to bump Chart.yaml version/appVersion from 0.2.0 to 0.10.0 (whether to also add kubeVersion constraint to server chart)
- Helm test template scope: what validations to run (pod readiness, endpoint connectivity, CRD existence, health probes)
- Chart alignment: whether to harmonize server chart templates with operator chart patterns (podLabels, PDB, priorityClass, topologySpread) or keep them as-is
- Test template naming and structure within templates/tests/ directories
- Exact wording and formatting of NOTES.txt output

</decisions>

<code_context>

## Existing Code Insights

### Reusable Assets

- `_helpers.tpl` (both charts): Standard name/fullname/labels/selectorLabels helpers, can be reused in NOTES.txt and test templates
- Operator `_helpers.tpl` has extra helpers: `credentialsSecretName`, `leaderElectionNamespace`
- Server deployment exposes port 8080 (http) with /health liveness/readiness probes
- Operator deployment exposes port 8080 (metrics) and 8081 (health) with /healthz and /readyz probes

### Established Patterns

- Both charts use apiVersion: v2, type: application
- Both charts currently at version 0.2.0 / appVersion 0.2.0
- Operator chart already has kubeVersion constraint (>=1.25.0-0), server chart does not
- Operator chart has annotations (category, licenses), server chart does not
- CRDs are managed via crds/ directory AND templates/crds/ (operator chart) with crds.install/crds.keep values

### Integration Points

- NOTES.txt files go in templates/ directory of each chart (standard Helm location)
- Test templates go in templates/tests/ directory of each chart
- Tests reference services and deployments created by existing templates
- Server chart service is named via `mcp-hangar.fullname`, operator metrics service is `mcp-hangar-operator.fullname`-metrics

</code_context>

<specifics>

## Specific Ideas

No specific requirements -- open to standard approaches for the areas under Claude's discretion.

</specifics>

<deferred>

## Deferred Ideas

None -- discussion stayed within phase scope.

</deferred>

---

*Phase: 07-helm-chart-maturity*
*Context gathered: 2026-03-01*
