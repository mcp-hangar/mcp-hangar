# Testing

## Quick Start

```bash
uv sync --extra dev
uv run pytest tests/ -v -m "not slow"
```

## Running Tests

```bash
# All unit tests
pytest tests/unit/ -v

# By marker
pytest tests/ -m unit
pytest tests/ -m integration
pytest tests/ -m "not container"

# Coverage
pytest tests/ -m "not slow" --cov=mcp_hangar --cov-report=html
```

### Markers

| Marker | Description |
|--------|-------------|
| `unit` | Fast, isolated, no external dependencies |
| `integration` | Multiple components working together |
| `container` | Requires Docker/Podman containers |
| `slow` | Long-running tests (>5 seconds) |
| `postgres` | Requires PostgreSQL container |
| `redis` | Requires Redis container |
| `langfuse` | Requires Langfuse container |
| `prometheus` | Requires Prometheus container |
| `property` | Property-based tests using Hypothesis |

## Testcontainers Integration

MCP Hangar uses [Testcontainers](https://testcontainers.com/) for integration tests with real services.

### Installation

```bash
pip install mcp-hangar[testcontainers]
# or
pip install "testcontainers[postgres]>=4.0.0" httpx
```

### Running Container Tests

```bash
# Run all container tests (requires Docker/Podman)
pytest tests/integration/containers/ --run-containers -v

# Run specific container tests
pytest tests/integration/containers/test_postgres.py --run-containers -v
pytest tests/integration/containers/test_langfuse.py --run-containers -v
pytest tests/integration/containers/test_redis.py --run-containers -v

# Skip slow container tests
pytest tests/integration/containers/ --run-containers -m "not slow" -v
```

### Available Container Fixtures

| Fixture | Description | Required Extra |
|---------|-------------|----------------|
| `postgres_container` | PostgreSQL 15 Alpine | `testcontainers[postgres]` |
| `redis_container` | Redis 7 Alpine | `testcontainers` |
| `langfuse_container` | Langfuse 2 with PostgreSQL | `testcontainers[postgres]` |
| `prometheus_container` | Prometheus v2.47 | `testcontainers` |
| `math_provider_container` | MCP Math Provider | Local image required |
| `sqlite_provider_container` | MCP SQLite Provider | Local image required |

### Example: Using PostgreSQL Container

```python
import pytest

@pytest.mark.container
@pytest.mark.postgres
def test_database_operations(postgres_container):
    """Test with real PostgreSQL database."""
    dsn = postgres_container["dsn"]

    import asyncpg
    conn = await asyncpg.connect(dsn)
    result = await conn.fetchval("SELECT 1")
    assert result == 1
```

### Example: Using Langfuse Container

```python
import pytest

@pytest.mark.container
@pytest.mark.langfuse
def test_langfuse_tracing(langfuse_config, langfuse_container, http_client):
    """Test with real Langfuse instance."""
    from mcp_hangar.infrastructure.observability.langfuse_adapter import (
        LangfuseObservabilityAdapter,
    )

    adapter = LangfuseObservabilityAdapter(langfuse_config)
    span = adapter.start_tool_span("test", "tool", {"arg": 1})
    span.end_success({"result": "ok"})
    adapter.flush()

    # Query Langfuse API
    response = http_client.get(
        f"{langfuse_container['url']}/api/public/traces",
        auth=(langfuse_container["public_key"], langfuse_container["secret_key"]),
    )
    assert response.status_code == 200
```

## Property-Based Testing

MCP Hangar uses [Hypothesis](https://hypothesis.readthedocs.io/) for property-based testing.

### Running Property Tests

```bash
# All property tests
pytest tests/unit/observability/test_property_based.py -v

# With more examples
pytest tests/unit/observability/test_property_based.py -v --hypothesis-seed=12345
```

### Example Property Test

```python
from hypothesis import given, strategies as st

@given(
    provider_name=st.text(min_size=1, max_size=50),
    tool_name=st.text(min_size=1, max_size=50),
)
def test_adapter_accepts_any_strings(provider_name, tool_name):
    """Adapter accepts any valid string inputs."""
    adapter = NullObservabilityAdapter()
    span = adapter.start_tool_span(provider_name, tool_name, {})
    assert isinstance(span, NullSpanHandle)
```

## Container Tests

```bash
# Build images first
podman build -t localhost/mcp-sqlite -f docker/Dockerfile.sqlite .

# Prepare data directory
mkdir -p data && chmod 777 data

# Run tests
pytest tests/feature/ -v
```

## Manual Testing

### Subprocess Provider

```yaml
# config.yaml
providers:
  math:
    mode: subprocess
    command: [python, tests/mock_provider.py]
```

```bash
python -m mcp_hangar.server
```

### Test via Python

```python
from mcp_hangar.provider_manager import ProviderManager
from mcp_hangar.models import ProviderSpec

spec = ProviderSpec(
    provider_id="test",
    mode="subprocess",
    command=["python", "tests/mock_provider.py"]
)

mgr = ProviderManager(spec)
mgr.ensure_ready()

result = mgr.invoke_tool("add", {"a": 5, "b": 3})
print(result)  # {"result": 8}

mgr.shutdown()
```

### Test Provider Directly

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tests/mock_provider.py
```

## Common Issues

### Provider won't start

```bash
# Test directly
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tests/mock_provider.py
```

### Permission denied (container)

```yaml
providers:
  memory:
    mode: container
    read_only: false
    volumes:
      - "./data:/app/data:rw"
```

### Tests hang

```bash
pytest tests/ -v --timeout=60
```
