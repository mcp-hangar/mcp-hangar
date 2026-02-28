# Technology Stack: v0.10 Additions

**Project:** MCP Hangar
**Researched:** 2026-02-28
**Scope:** New stack additions for v0.10 milestone only (existing stack already validated)

## Existing Stack (Not Re-Researched)

Already in place and validated: Python 3.11+, Go 1.23.0, controller-runtime v0.17.0, k8s libs v0.29.0, MkDocs with Material theme, mkdocstrings plugin (basic config), Helm charts v0.2.0, Prometheus metrics, structlog, Langfuse tracing.

---

## Recommended Stack Additions

### Documentation: API Reference Generation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| mkdocstrings-python | 2.0.3 | Python handler for mkdocstrings | Already implicitly used by mkdocstrings plugin in mkdocs.yml, but needs explicit dependency pinning and enhanced configuration. Uses Griffe for AST-based analysis -- no import side effects. Supports Google-style docstrings matching project convention. |

**Integration notes:**

- mkdocstrings (v1.0.3) is already configured in `mkdocs.yml` but with minimal options (`show_source: true`, `show_root_heading: true`)
- mkdocstrings-python needs to be added to a docs dependency group in `pyproject.toml` (currently no docs deps exist)
- Enhanced configuration needed for production-quality API reference:

```yaml
# mkdocs.yml - enhanced mkdocstrings config
plugins:
  - mkdocstrings:
      handlers:
        python:
          options:
            docstring_style: google
            docstring_section_style: spacy
            show_source: true
            show_root_heading: true
            show_signature_annotations: true
            show_symbol_type_heading: true
            show_symbol_type_toc: true
            members_order: source
            merge_init_into_class: true
            separate_signature: true
            signature_crossrefs: true
            filters:
              - "!^_"  # Hide private members
```

- API reference pages use `::: mcp_hangar.domain.model.provider` directive syntax to auto-generate docs from docstrings
- Griffe handles static analysis so no runtime imports are needed (critical for a project with many infrastructure deps)

### Documentation: Link Validation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| mkdocs-htmlproofer-plugin | 1.5.0 | Validates links in rendered HTML | Actively maintained (last release Feb 23, 2026). Catches broken internal links, anchors, and external URLs during `mkdocs build`. Integrates as a standard MkDocs plugin -- zero config beyond adding to plugins list. |

**Why not mkdocs-linkcheck:** Last updated August 2021 (v1.0.6). Stale and unmaintained. Do not use.

**Integration:**

```yaml
# mkdocs.yml
plugins:
  - htmlproofer:
      raise_error: true  # Fail CI on broken links
```

### Helm Chart Testing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| helm-unittest | 1.0.3 | Unit testing Helm chart templates | Helm plugin that renders templates with test values and asserts on output. Tests live alongside charts in `tests/` directories. No cluster needed. Fast CI feedback. |
| chart-testing (ct) | 3.14.0 | Chart linting and integration testing | Official Helm project tool. Validates Chart.yaml, values schema, template rendering. `ct lint` for static checks, `ct install` for cluster-based integration tests (pairs with kind). |

**Integration notes:**

- helm-unittest tests go in `packages/helm-charts/mcp-hangar/tests/` and `packages/helm-charts/mcp-hangar-operator/tests/`
- Each test file is YAML defining test cases with assertions on rendered manifests
- chart-testing runs from CI -- `ct lint --charts packages/helm-charts/mcp-hangar` for linting, `ct install` with a kind cluster for integration
- Neither chart has any tests currently -- this is net-new

**Example helm-unittest test structure:**

```yaml
# packages/helm-charts/mcp-hangar-operator/tests/deployment_test.yaml
suite: operator deployment
templates:
  - templates/deployment.yaml
tests:
  - it: should set correct image
    set:
      image.repository: ghcr.io/example/mcp-hangar-operator
      image.tag: v0.10.0
    asserts:
      - equal:
          path: spec.template.spec.containers[0].image
          value: ghcr.io/example/mcp-hangar-operator:v0.10.0
```

### Go Operator: Controller Testing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| controller-runtime/pkg/envtest | v0.17.0 (already in go.mod) | Integration testing with real API server | Already a transitive dependency. Spins up a local etcd + API server for realistic controller testing. The Makefile already has `setup-envtest` configured with `ENVTEST_K8S_VERSION = 1.29.0`. Just needs actual test files written. |
| ginkgo/v2 | v2.27.5 (already indirect in go.mod) | BDD test framework for Go | Already an indirect dependency. Standard in the controller-runtime ecosystem. Promote to direct dependency when writing controller integration tests. |
| gomega | v1.39.0 (already indirect in go.mod) | Matcher library for ginkgo | Always paired with ginkgo. Already indirect dependency. Promote to direct when used. |

**Integration notes:**

- No new dependencies to install -- envtest, ginkgo, and gomega are already in `go.mod` (indirect)
- `setup-envtest` is already configured in the Makefile but no integration test files exist
- MCPProviderGroup and MCPDiscoverySource controllers need to be implemented first, then tested with envtest
- The existing MCPProvider controller (`mcpprovider_controller.go`, 535 lines) serves as the pattern template for new controllers
- Test files go in `packages/operator/internal/controller/` alongside the controllers

**envtest test pattern (matches existing project conventions):**

```go
// controller_integration_test.go
var testEnv *envtest.Environment
var k8sClient client.Client

func TestControllers(t *testing.T) {
    RegisterFailHandler(Fail)
    RunSpecs(t, "Controller Suite")
}

var _ = BeforeSuite(func() {
    testEnv = &envtest.Environment{
        CRDDirectoryPaths: []string{
            filepath.Join("..", "..", "config", "crd", "bases"),
        },
    }
    cfg, err := testEnv.Start()
    Expect(err).NotTo(HaveOccurred())
    // ... setup client, manager, controllers
})
```

### Go Operator: New Controller Dependencies

No new Go dependencies are needed for the MCPProviderGroup and MCPDiscoverySource controllers. The existing stack covers everything:

| Existing Dependency | Used For |
|---------------------|----------|
| controller-runtime v0.17.0 | Reconciler framework, client, manager, builder |
| k8s.io/api v0.29.0 | Core k8s types (Pod, Service, ConfigMap) |
| k8s.io/apimachinery v0.29.0 | Meta types, labels, field selectors |
| k8s.io/client-go v0.29.0 | REST client, informers, auth |
| controller-gen v0.14.0 | CRD generation from Go types (already in Makefile) |

The API types for both MCPProviderGroup (`mcpprovidergroup_types.go`, 264 lines) and MCPDiscoverySource (`mcpdiscoverysource_types.go`, 297 lines) are already defined. Implementation follows the existing MCPProvider controller pattern.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Link validation | mkdocs-htmlproofer-plugin 1.5.0 | mkdocs-linkcheck 1.0.6 | Last updated Aug 2021. Stale, unmaintained. htmlproofer is actively maintained (Feb 2026). |
| Helm unit testing | helm-unittest 1.0.3 | Raw `helm template` + yq scripting | helm-unittest provides declarative test syntax, snapshot testing, and structured assertions. Shell scripting is fragile and hard to maintain. |
| Helm linting | chart-testing (ct) 3.14.0 | `helm lint` only | ct adds schema validation, version checking, and changed-chart detection for monorepo CI. Superset of `helm lint`. |
| Go test framework | ginkgo/v2 + gomega | Standard `testing` only | ginkgo is the controller-runtime ecosystem standard. envtest examples universally use ginkgo. Existing indirect dependency -- zero cost to adopt. |
| API doc generation | mkdocstrings-python (Griffe) | Sphinx + autodoc | Project already uses MkDocs with Material theme. Switching to Sphinx would require rewriting all existing docs. mkdocstrings integrates natively. |
| API doc generation | mkdocstrings-python (Griffe) | pdoc3 | pdoc generates standalone HTML, not integrated into MkDocs site. Would create a separate docs site requiring its own hosting and navigation. |

---

## What NOT to Use

| Technology | Reason |
|------------|--------|
| mkdocs-linkcheck | Unmaintained since 2021. Use mkdocs-htmlproofer-plugin instead. |
| Sphinx / autodoc | Project is committed to MkDocs + Material. Switching doc frameworks mid-project for one feature is wasteful. |
| kubebuilder scaffolding | Operator structure already exists. Re-scaffolding would overwrite existing customizations. Write new controllers by hand following established patterns. |
| Operator SDK | Project uses raw controller-runtime directly. Adding the Operator SDK abstraction layer on top adds complexity without value for this project's needs. |
| kuttl (test tool) | Heavy e2e test framework for operators. Overkill when envtest provides sufficient integration testing for controller logic. Consider only if multi-cluster scenarios are needed later. |

---

## Installation

### Python docs dependencies (add to pyproject.toml)

```toml
[project.optional-dependencies]
docs = [
    "mkdocs>=1.6.0",
    "mkdocs-material>=9.5.0",
    "mkdocstrings[python]>=1.0.0",
    "mkdocs-htmlproofer-plugin>=1.5.0",
    "mkdocs-git-revision-date-localized-plugin>=1.2.0",
]
```

### Helm testing tools (CI setup)

```bash
# helm-unittest plugin
helm plugin install https://github.com/helm-unittest/helm-unittest --version 1.0.3

# chart-testing
# Install via package manager or download binary
brew install chart-testing  # macOS
# or: pip install chart-testing
```

### Go testing (no action needed)

```bash
# envtest binaries (already in Makefile)
make envtest

# ginkgo CLI (for running BDD tests)
go install github.com/onsi/ginkgo/v2/ginkgo@v2.27.5
```

---

## Sources

| Source | Confidence | What It Verified |
|--------|------------|------------------|
| mkdocs.yml (project file) | HIGH | Current mkdocstrings config, Material theme setup |
| go.mod (project file) | HIGH | Exact Go dependency versions, indirect deps |
| Makefile (project file) | HIGH | Existing envtest, controller-gen, golangci-lint setup |
| Operator source code (project files) | HIGH | Controller patterns, API types, test structure |
| pyproject.toml (project file) | HIGH | Absence of docs dependencies |
| PyPI package pages | HIGH | mkdocstrings v1.0.3, mkdocstrings-python v2.0.3, htmlproofer v1.5.0 release dates |
| GitHub releases | MEDIUM | helm-unittest v1.0.3, chart-testing v3.14.0 versions |
| mkdocstrings-python docs | HIGH | Configuration options, Griffe-based analysis, Google docstring support |
