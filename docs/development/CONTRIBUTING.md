# Contributing

## Setup

```bash
git clone https://github.com/mcp-hangar/mcp-hangar.git
cd mcp-hangar

# Install with dev dependencies
pip install -e ".[dev]"

# Or use root Makefile
make setup
```

## Monorepo Structure

MCP Hangar is a monorepo:

```
mcp-hangar/
├── src/mcp_hangar/          # Python package (PyPI: mcp-hangar) -- MIT
├── enterprise/              # BSL 1.1 licensed features
│   ├── auth/                # RBAC, API key, JWT/OIDC
│   ├── behavioral/          # Network profiling, deviation detection
│   ├── identity/            # Caller identity propagation, audit
│   ├── compliance/          # SIEM export (CEF, LEEF, JSON-lines)
│   ├── persistence/         # SQLite/Postgres event stores
│   ├── semantic/            # Pattern engine, detection rules
│   └── integrations/        # Langfuse adapter
├── tests/                   # Python tests
├── packages/
│   ├── operator/            # Kubernetes operator (Go)
│   │   ├── api/             # CRD definitions
│   │   ├── cmd/             # Main entrypoints
│   │   ├── internal/        # Controller logic
│   │   └── go.mod           # Go module config
│   ├── ui/                  # React dashboard
│   └── helm-charts/         # Helm charts
│       ├── mcp-hangar/      # Core Helm chart
│       └── mcp-hangar-operator/  # Operator Helm chart
├── docs/                    # MkDocs documentation
├── examples/                # Quick starts, OTEL recipes
├── monitoring/              # Grafana, Prometheus configs
└── Makefile                 # Root orchestration
```

## Python Core Structure

```
src/mcp_hangar/
├── domain/           # DDD domain layer
│   ├── model/        # Aggregates, entities
│   ├── services/     # Domain services
│   ├── events.py     # Domain events
│   ├── contracts/    # Interfaces consumed by enterprise/
│   └── exceptions.py
├── application/      # Application layer
│   ├── commands/     # CQRS commands
│   ├── queries/      # CQRS queries
│   ├── ports/        # Port interfaces consumed by enterprise/
│   └── sagas/
├── infrastructure/   # Infrastructure adapters
│   └── observability/  # OTLPAuditExporter
├── observability/    # Conventions, tracing, metrics, health
├── server/           # MCP server module
│   ├── bootstrap/    # DI composition root
│   ├── config.py     # Configuration loading
│   ├── state.py      # Global state management
│   └── tools/        # MCP tool implementations
├── stdio_client.py   # JSON-RPC client
└── gc.py             # Background workers
```

## Licensing

- **Core** (`src/`) -- MIT. No CLA required.
- **Enterprise** (`enterprise/`) -- BSL 1.1. CLA required for contributions. See [CLA.md](../cla.md).
- Core must **never** import from `enterprise/`. CI enforces this boundary.

## Code Style

```bash
ruff check src tests --fix
ruff format src tests
mypy src/mcp_hangar
```

### Conventions

| Item | Style |
|------|-------|
| Classes | `PascalCase` |
| Functions | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Events | `PascalCase` + past tense (`McpServerStarted`) |

### Type Hints

Required for all new code. Use Python 3.11+ built-in generics:

```python
def invoke_tool(
    self,
    tool_name: str,
    arguments: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    ...
```

## Testing

```bash
pytest -v -m "not slow"
pytest --cov=mcp_hangar --cov-report=html

# Or from root
make test
```

Target: >80% coverage on new code.

### Writing Tests

```python
def test_tool_invocation():
    # Arrange
    mcp_server = McpServer(mcp_server_id="test", mode="subprocess", command=[...])

    # Act
    result = mcp_server.invoke_tool("add", {"a": 1, "b": 2})

    # Assert
    assert result["result"] == 3
```

## Pull Requests

See [Git Flow](GIT_FLOW.md) for branching conventions, merge strategy, and commit scopes.

1. Create feature branch
2. Make changes following style guidelines
3. Add tests
4. Run checks:

   ```bash
   pre-commit install --hook-type pre-commit
   pytest -v
   pre-commit run --all-files
   ```

5. Update docs if needed

### PR Template

PRs must follow the template in [`.github/PULL_REQUEST_TEMPLATE.md`](https://github.com/mcp-hangar/mcp-hangar/blob/main/.github/PULL_REQUEST_TEMPLATE.md). Required sections are enforced by the `pr-body / validate` CI check.

## Architecture Guidelines

**Value Objects:**

```python
mcp_server_id = ProviderId("my-mcp-server")  # Validated
```

**Events:**

```python
mcp_server.ensure_ready()
for event in mcp_server.collect_events():
    event_bus.publish(event)
```

**Exceptions:**

```python
# Basic usage
raise McpServerStartError(
    mcp_server_id="my-mcp-server",
    reason="Connection refused"
)

# With diagnostics (preferred)
raise McpServerStartError(
    mcp_server_id="my-mcp-server",
    reason="MCP initialization failed: process crashed",
    stderr="ModuleNotFoundError: No module named 'requests'",
    exit_code=1,
    suggestion="Install missing Python dependencies."
)

# Get user-friendly message
try:
    mcp_server.ensure_ready()
except McpServerStartError as e:
    print(e.get_user_message())
```

**Logging:**

```python
logger.info("mcp_server_started: %s, mode=%s", mcp_server_id, mode)
```

## Releasing

### Release Process Overview

MCP Hangar uses automated CI/CD for releases. The process ensures quality through:

1. **Version Validation** — Tag must match `pyproject.toml` version
2. **Full Test Suite** — All tests across Python 3.11-3.14
3. **Security Scanning** — Dependency audit and container scanning
4. **Artifact Publishing** — PyPI package and Docker images

### Creating a Release

Releases are automated via [release-please](https://github.com/googleapis/release-please). When Conventional Commit PRs merge to `main`, release-please maintains a long-running Release PR that bumps the version and updates the changelog. Merging that PR creates the version tag, which `release.yml` consumes to publish to PyPI and GHCR.

See [RELEASE.md](../runbooks/RELEASE.md) for the full operational runbook.

### Pre-release Versions

Pre-releases are automatically published to **TestPyPI**:

```bash
# Tag patterns for pre-releases
v1.0.0-alpha.1  # Alpha release
v1.0.0-beta.1   # Beta release
v1.0.0-rc.1     # Release candidate
```

Install pre-release:

```bash
pip install --index-url https://test.pypi.org/simple/ mcp-hangar==1.0.0rc1
```

### Release Checklist

Before releasing, ensure:

- [ ] All tests pass locally: `pytest -v`
- [ ] Linting passes: `pre-commit run --all-files`
- [ ] CHANGELOG.md is updated with all notable changes
- [ ] Documentation is updated for new features
- [ ] Breaking changes are clearly documented
- [ ] Version follows [Semantic Versioning](https://semver.org/)

### Versioning Guidelines

We follow Semantic Versioning (SemVer):

| Change Type | Version Bump | Example |
|-------------|--------------|---------|
| Bug fixes, patches | PATCH | 1.0.0 → 1.0.1 |
| New features (backward-compatible) | MINOR | 1.0.1 → 1.1.0 |
| Breaking changes | MAJOR | 1.1.0 → 2.0.0 |

### Release Artifacts

Each release produces:

| Artifact | Location | Tags |
|----------|----------|------|
| Python Package | [PyPI](https://pypi.org/project/mcp-hangar/) | Version number |
| Docker Image | [GHCR](https://ghcr.io/mcp-hangar/mcp-hangar) | `latest`, `X.Y.Z`, `X.Y`, `X` |
| GitHub Release | Repository Releases | Changelog, install instructions |

### Hotfix Process

For urgent fixes on released versions, follow the [HOTFIX_RUNBOOK.md](HOTFIX_RUNBOOK.md).

## Licensing Model

MCP Hangar uses a dual-license model:

| Directory | License | CLA Required |
|-----------|---------|--------------|
| `src/mcp_hangar/` | MIT | No |
| `tests/`, `docs/`, `examples/`, `monitoring/` | MIT | No |
| `enterprise/` | BSL 1.1 | **Yes** |

### Contributing to enterprise/

Contributions to `enterprise/` require agreeing to the [Contributor License Agreement](../cla.md). Include this statement in your PR description:

> I have read and agree to the MCP Hangar Contributor License Agreement (CLA.md). My contribution to enterprise/ is my original work and I grant the rights described therein.

See [CLA.md](../cla.md) for full terms. Core (MIT) contributions do not require a CLA.

## Code of Conduct

Please read our [Code of Conduct](../code-of-conduct.md) before contributing.

## First Contribution?

Look for issues labeled [`good first issue`](https://github.com/mcp-hangar/mcp-hangar/labels/good%20first%20issue).

Questions? Open a [Discussion](https://github.com/mcp-hangar/mcp-hangar/discussions).
