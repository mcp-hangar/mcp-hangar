# Contributing

## Setup

```bash
git clone https://github.com/mapyr/mcp-hangar.git
cd mcp-hangar
uv sync --extra dev
uv run pre-commit install
```

## Project Structure

```
mcp_hangar/
├── domain/           # DDD domain layer
│   ├── model/        # Aggregates, entities
│   ├── services/     # Domain services
│   ├── events.py     # Domain events
│   └── exceptions.py
├── application/      # Application layer
│   ├── commands/     # CQRS commands
│   ├── queries/      # CQRS queries
│   └── sagas/
├── infrastructure/   # Infrastructure adapters
├── server/           # MCP server module
│   ├── __init__.py   # Main entry point
│   ├── config.py     # Configuration loading
│   ├── state.py      # Global state management
│   └── tools/        # MCP tool implementations
├── observability/    # Metrics, tracing, health
├── stdio_client.py   # JSON-RPC client
└── gc.py             # Background workers
```

## Code Style

```bash
black mcp_hangar/ tests/
isort mcp_hangar/ tests/
ruff check mcp_hangar/ tests/ --fix
```

### Conventions

| Item | Style |
|------|-------|
| Classes | `PascalCase` |
| Functions | `snake_case` |
| Constants | `UPPER_SNAKE_CASE` |
| Events | `PascalCase` + past tense (`ProviderStarted`) |

### Type Hints

Required for all new code. Use Python 3.10+ built-in generics:

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
uv run pytest tests/ -v -m "not slow"
uv run pytest tests/ --cov=mcp_hangar --cov-report=html
```

Target: >80% coverage on new code.

### Writing Tests

```python
def test_tool_invocation():
    # Arrange
    provider = Provider(provider_id="test", mode="subprocess", command=[...])

    # Act
    result = provider.invoke_tool("add", {"a": 1, "b": 2})

    # Assert
    assert result["result"] == 3
```

## Pull Requests

1. Create feature branch
2. Make changes following style guidelines
3. Add tests
4. Run checks:
   ```bash
   uv run pytest tests/ -v -m "not slow"
   uv run pre-commit run --all-files
   ```
5. Update docs if needed

### PR Template

```markdown
## Description
Brief description.

## Type
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change

## Testing
- [ ] Unit tests added
- [ ] All tests pass
```

## Architecture Guidelines

**Value Objects:**
```python
provider_id = ProviderId("my-provider")  # Validated
```

**Events:**
```python
provider.ensure_ready()
for event in provider.collect_events():
    event_bus.publish(event)
```

**Exceptions:**
```python
raise ProviderStartError(
    provider_id="my-provider",
    reason="Connection refused"
)
```

**Logging:**
```python
logger.info("provider_started: %s, mode=%s", provider_id, mode)
```

## Releasing

### Release Process Overview

MCP Hangar uses automated CI/CD for releases. The process ensures quality through:

1. **Version Validation** — Tag must match `pyproject.toml` version
2. **Full Test Suite** — All tests across Python 3.10-3.13
3. **Security Scanning** — Dependency audit and container scanning
4. **Artifact Publishing** — PyPI package and Docker images

### Creating a Release

#### Option 1: Automated (Recommended)

Use the GitHub Actions workflow:

1. Go to **Actions** → **Version Bump**
2. Click **Run workflow**
3. Select bump type: `patch`, `minor`, or `major`
4. Optionally select pre-release suffix (`alpha.1`, `beta.1`, `rc.1`)
5. Run (or use **dry run** to preview)

The workflow will:
- Update version in `pyproject.toml`
- Update `CHANGELOG.md` with release date
- Create and push the version tag
- Trigger the release pipeline automatically

#### Option 2: Manual

```bash
# 1. Update version in pyproject.toml
sed -i 's/version = ".*"/version = "1.2.0"/' pyproject.toml

# 2. Update CHANGELOG.md - move Unreleased items to new version section

# 3. Commit changes
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to 1.2.0"

# 4. Create annotated tag
git tag -a v1.2.0 -m "Release v1.2.0"

# 5. Push
git push origin main
git push origin v1.2.0
```

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

- [ ] All tests pass locally: `uv run pytest tests/ -v`
- [ ] Linting passes: `uv run pre-commit run --all-files`
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
| Docker Image | [GHCR](https://ghcr.io/mapyr/mcp-hangar) | `latest`, `X.Y.Z`, `X.Y`, `X` |
| GitHub Release | Repository Releases | Changelog, install instructions |

### Hotfix Process

For urgent fixes on released versions:

```bash
# 1. Create hotfix branch from tag
git checkout -b hotfix/1.0.1 v1.0.0

# 2. Apply fix, add tests

# 3. Update version and changelog
# 4. Tag and push

git tag -a v1.0.1 -m "Hotfix: description"
git push origin v1.0.1

# 5. Cherry-pick to main if applicable
git checkout main
git cherry-pick <commit-hash>
```

## License

MIT

## Code of Conduct

Please read our [Code of Conduct](../code-of-conduct.md) before contributing.

## First Contribution?

Look for issues labeled [`good first issue`](https://github.com/mapyr/mcp-hangar/labels/good%20first%20issue).

Questions? Open a [Discussion](https://github.com/mapyr/mcp-hangar/discussions).
