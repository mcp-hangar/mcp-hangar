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
logger.info(f"provider_started: {provider_id}, mode={mode}")
```

## License

MIT
