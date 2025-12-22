# Testing Guide

This guide covers all aspects of testing MCP Hangar, from local development to CI/CD integration.

## Prerequisites

- Python 3.10+
- pip or uv
- Docker or Podman (optional, for container provider tests)

## Quick Start

```bash
cd mcp-hangar
pip install -e ".[dev]"

# Run all fast tests
pytest tests/ -v -m "not slow"
```

Expected output:

```
========================= test session starts =========================
platform linux -- Python 3.11.x, pytest-7.x.x
collected XX items

tests/unit/test_value_objects.py::test_provider_id_validation PASSED
tests/unit/test_events.py::test_provider_started_event PASSED
tests/integration/test_provider_manager.py::test_provider_lifecycle PASSED
...
========================= XX passed in X.XXs =========================
```

---

## Running Tests

### Unit Tests

```bash
# Run all tests
pytest tests/ -v

# Quick tests only (skip slow tests)
pytest tests/ -v -m "not slow"

# Run specific test file
pytest tests/integration/test_provider_manager.py -v

# Run tests by marker
pytest tests/ -v -m unit
pytest tests/ -v -m integration
pytest tests/ -v -m docker
```

### Test Coverage

```bash
pytest tests/ -m "not slow" --cov=mcp_hangar --cov-report=html
# Open htmlcov/index.html in browser
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `unit` | Fast, isolated unit tests |
| `integration` | Tests that involve multiple components |
| `slow` | Long-running tests |
| `docker` | Tests requiring Docker/Podman |
| `security` | Security-related tests |

---

## Local Development Testing

### Scenario 1: Subprocess Provider

#### Step 1: Environment setup

```bash
cd mcp-hangar
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

#### Step 2: Create configuration file

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` to use the mock provider:

```yaml
providers:
  math_subprocess:
    mode: subprocess
    command:
      - python
      - tests/mock_provider.py
    idle_ttl_s: 180
    health_check_interval_s: 60
    max_consecutive_failures: 3
```

#### Step 3: Run Registry Server

```bash
python -m mcp_hangar.server
```

You should see startup logs:

```json
{"timestamp": "...", "level": "INFO", "message": "mcp_registry_starting", ...}
{"timestamp": "...", "level": "INFO", "message": "mcp_registry_ready: providers=['math_subprocess']", ...}
```

#### Step 4: Test via Python API

```python
import sys
from mcp_hangar.provider_manager import ProviderManager
from mcp_hangar.models import ProviderSpec

spec = ProviderSpec(
    provider_id="math_test",
    mode="subprocess",
    command=[sys.executable, "tests/mock_provider.py"],
    idle_ttl_s=300
)

manager = ProviderManager(spec)
print(f"Initial state: {manager.state}")

manager.ensure_ready()
print(f"After start: {manager.state}")

tools = manager.get_tool_names()
print(f"Available tools: {tools}")

result = manager.invoke_tool("add", {"a": 5, "b": 3})
print(f"5 + 3 = {result['result']}")

manager.shutdown()
print(f"Final state: {manager.state}")
```

### Scenario 2: Interactive REPL Testing

```python
>>> import sys
>>> from mcp_hangar.provider_manager import ProviderManager
>>> from mcp_hangar.models import ProviderSpec
>>>
>>> spec = ProviderSpec(
...     provider_id="test_math",
...     mode="subprocess",
...     command=[sys.executable, "tests/mock_provider.py"],
...     idle_ttl_s=300
... )
>>>
>>> manager = ProviderManager(spec)
>>> manager.ensure_ready()
>>> print(f"State: {manager.state}")
State: ready
>>>
>>> tools = manager.get_tool_names()
>>> print(f"Available tools: {tools}")
Available tools: ['add', 'subtract', 'multiply', 'divide', 'power', 'echo']
>>>
>>> result = manager.invoke_tool("add", {"a": 10, "b": 20})
>>> print(f"10 + 20 = {result['result']}")
10 + 20 = 30
>>>
>>> manager.shutdown()
```

### Scenario 3: Garbage Collection and Health Monitoring

Test automatic shutdown of idle providers:

```python
import sys
import time
from mcp_hangar.provider_manager import ProviderManager
from mcp_hangar.models import ProviderSpec
from mcp_hangar.gc import BackgroundWorker

spec = ProviderSpec(
    provider_id="gc_test",
    mode="subprocess",
    command=[sys.executable, "tests/mock_provider.py"],
    idle_ttl_s=5  # Short TTL for testing
)

manager = ProviderManager(spec)
providers = {"gc_test": manager}

manager.ensure_ready()
print(f"Provider state: {manager.state}")  # ready

gc_worker = BackgroundWorker(providers, interval_s=2, task="gc")
gc_worker.start()
print("GC worker started")

result = manager.invoke_tool("add", {"a": 1, "b": 2})
print(f"1 + 2 = {result['result']}")

print("Waiting for idle timeout...")
time.sleep(8)

print(f"Provider state after GC: {manager.state}")  # cold

gc_worker.stop()
```

---

## Container Provider Testing

### Building Container Images

```bash
podman build -t mcp-math:latest -f docker/Dockerfile.math .
podman build -t mcp-memory:latest -f docker/Dockerfile.memory .
podman build -t mcp-filesystem:latest -f docker/Dockerfile.filesystem .
podman build -t mcp-fetch:latest -f docker/Dockerfile.fetch .
```

### Container Configuration

```yaml
providers:
  math_subprocess:
    mode: subprocess
    command:
      - python
      - tests/mock_provider.py
    idle_ttl_s: 180

  math_docker:
    mode: container
    image: mcp-math:latest
    idle_ttl_s: 300
    health_check_interval_s: 60
    max_consecutive_failures: 3
```

### Running Container Tests

```bash
# Run all container-related tests
pytest tests/feature/ -v

# Run specific container tests
pytest tests/feature/test_all_providers.py -v
pytest tests/feature/test_memory_permissions.py -v
pytest tests/feature/test_prebuilt_image.py -v
```

Before running container tests, ensure:

1. Container images are built (see above)
2. Data directory exists for memory provider:
   ```bash
   mkdir -p data
   chmod 755 data
   ```

### Container Test Requirements

| Provider | Network | Read-Only | User Mapping | Notes |
|----------|---------|-----------|--------------|-------|
| filesystem | none | true | current | Needs host user for file access |
| memory | none | false | - | Needs writable `/app/data` |
| fetch | bridge | true | - | Needs network access |
| math | none | true | - | Fully isolated |

### Container Security Settings

All providers run with security restrictions:

- `--cap-drop ALL` - No capabilities
- `--security-opt no-new-privileges` - No privilege escalation
- Resource limits (memory, CPU)
- Network isolation (except fetch)
- Read-only root filesystem (except memory)

---

## Docker Compose Testing

```bash
# Start MCP Hangar
docker-compose up -d

# View logs
docker logs -f mcp-hangar

# Run with provider containers
docker-compose --profile with-providers up -d
```

---

## Common Issues and Solutions

### "ModuleNotFoundError: No module named 'mcp'"

```bash
pip install mcp
```

### "FileNotFoundError: config.yaml"

```bash
cp config.yaml.example config.yaml
```

### Provider doesn't start / "EOF on stdout"

1. Test the provider directly:
   ```bash
   echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | python tests/mock_provider.py
   ```

2. Use `tests/mock_provider.py` instead of other providers for testing:
   ```yaml
   command:
     - python
     - tests/mock_provider.py
   ```

### "ProviderDegradedError" / backoff

Provider is in degraded state after previous errors. Wait for backoff reset or create a new ProviderManager.

### Memory Provider - Permission Denied

**Error:** `EACCES: permission denied, open '/app/data/memory.jsonl'`

**Solution:**
```yaml
providers:
  memory:
    mode: container
    image: mcp-memory:latest
    read_only: false
    volumes:
      - "./data:/app/data:rw"
```

### Filesystem Provider - Permission Denied

**Error:** `EACCES: permission denied, realpath '/data/.mcp_test_file.txt'`

**Solution:**
```yaml
providers:
  filesystem:
    mode: container
    image: mcp-filesystem:latest
    user: "current"
    volumes:
      - "${HOME}:/data:ro"
```

### Fetch Provider - Network Error

```bash
# Verify network connectivity
curl -I https://github.com
podman run --rm --network bridge alpine ping -c 1 github.com
```

### Container image not found

```bash
# Verify images exist
podman images | grep mcp-

# Rebuild if needed
podman build -t mcp-math:latest -f docker/Dockerfile.math .
```

### Tests hang indefinitely

```bash
# Run with timeout
pytest tests/ -v --timeout=60

# Check for zombie processes
ps aux | grep python
```

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.10', '3.11', '3.12', '3.13']

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: pip install .[dev]

      - name: Run tests
        run: pytest tests/ -v -m "not slow and not docker"

      - name: Run linting
        run: |
          black --check mcp_hangar/ tests/
          ruff check mcp_hangar/ tests/
```

### Container Tests in CI

```yaml
test-containers:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4

    - name: Build images
      run: |
        podman build -t mcp-memory:latest -f docker/Dockerfile.memory .
        podman build -t mcp-filesystem:latest -f docker/Dockerfile.filesystem .
        podman build -t mcp-fetch:latest -f docker/Dockerfile.fetch .
        podman build -t mcp-math:latest -f docker/Dockerfile.math .

    - name: Setup test data
      run: mkdir -p data

    - name: Run container tests
      run: pytest tests/feature/ -v
```

---

## Expected Test Results

After successful setup you should be able to:

- [ ] List available providers (`registry_list`)
- [ ] Start a provider (`registry_start`)
- [ ] See available tools (`registry_tools`)
- [ ] Invoke math operations:
  - `add(5, 3)` → `{"result": 8}`
  - `multiply(7, 4)` → `{"result": 28}`
  - `divide(10, 2)` → `{"result": 5.0}`
  - `power(2, 8)` → `{"result": 256}`
- [ ] Check registry health (`registry_health`)
- [ ] Stop a provider (`registry_stop`)

## What the Tests Verify

- **Hot-loading**: Providers start only when needed
- **Health monitoring**: Background worker checks provider status
- **Garbage collection**: Unused providers shut down after `idle_ttl_s`
- **State machine**: Provider transitions through COLD → INITIALIZING → READY
- **Tool discovery**: Automatic discovery of tools from provider
- **Tool invocation**: Invoking tools with argument passing
- **Error handling**: Proper error propagation and circuit breaker
- **Security**: Rate limiting and input validation
- **Container isolation**: Security restrictions applied correctly
