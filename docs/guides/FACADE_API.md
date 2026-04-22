<!-- markdownlint-disable MD046 -->

# Facade API

Programmatic Python interface to MCP Hangar for embedding MCP server management in applications and services.

## Quick Start

=== "Async"

    ```python
    from mcp_hangar import Hangar

    async with Hangar.from_config("config.yaml") as hangar:
        result = await hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)
    ```

=== "Sync"

    ```python
    from mcp_hangar import SyncHangar

    with SyncHangar.from_config("config.yaml") as hangar:
        result = hangar.invoke("math", "add", {"a": 1, "b": 2})
        print(result)
    ```

## Creating a Hangar Instance

Three ways to create a Hangar instance:

### From YAML Config File

Load MCP server definitions from a `config.yaml` file:

=== "Async"

    ```python
    hangar = Hangar.from_config("config.yaml")
    ```

=== "Sync"

    ```python
    hangar = SyncHangar.from_config("config.yaml")
    ```

### From Builder (Programmatic)

Use the `HangarConfig` builder to define MCP servers in code:

=== "Async"

    ```python
    from mcp_hangar import Hangar, HangarConfig

    config = (
        HangarConfig()
        .add_mcp_server("math", mode="subprocess", command=["python", "-m", "math_server"])
        .add_mcp_server("fetch", mode="remote", url="https://fetch.example.com/mcp")
        .max_concurrency(30)
        .build()
    )

    hangar = Hangar.from_builder(config)
    ```

=== "Sync"

    ```python
    from mcp_hangar import SyncHangar, HangarConfig

    config = (
        HangarConfig()
        .add_mcp_server("math", mode="subprocess", command=["python", "-m", "math_server"])
        .add_mcp_server("fetch", mode="remote", url="https://fetch.example.com/mcp")
        .max_concurrency(30)
        .build()
    )

    hangar = SyncHangar.from_builder(config)
    ```

### Direct Constructor

Pass a config path or pre-built config data directly:

```python
hangar = Hangar(config_path="config.yaml")
# or
hangar = Hangar(config=config_data)
```

## HangarConfig Builder

The `HangarConfig` builder provides a fluent API for programmatic configuration. Once `.build()` is called, the config is frozen and cannot be modified.

### Builder Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `HangarConfig()` | `HangarConfig` | Create an empty config builder |
| `.add_mcp_server(name, ...)` | `self` | Add a MCP server definition |
| `.enable_discovery(...)` | `self` | Enable discovery sources |
| `.max_concurrency(value)` | `self` | Set thread pool size for `invoke()` |
| `.set_intervals(...)` | `self` | Set background worker intervals |
| `.build()` | `HangarConfigData` | Build and validate the configuration |
| `.to_dict()` | `dict` | Convert to YAML-compatible dict format |

### `.add_mcp_server()` Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | required | Unique MCP server identifier |
| `mode` | `str` | `"subprocess"` | MCP Server mode: `subprocess`, `docker`, or `remote` |
| `command` | `list[str] \| None` | `None` | Command for subprocess mode (required for subprocess) |
| `image` | `str \| None` | `None` | Docker image for docker mode (required for docker) |
| `url` | `str \| None` | `None` | HTTP endpoint for remote mode (required for remote) |
| `env` | `dict \| None` | `None` | Environment variables for the MCP server process |
| `idle_ttl_s` | `int` | `300` | Seconds before auto-shutdown when idle |

### `.enable_discovery()` Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `docker` | `bool` | `False` | Enable Docker label discovery |
| `kubernetes` | `bool` | `False` | Enable Kubernetes annotation discovery |
| `filesystem` | `list[str] \| None` | `None` | Filesystem paths to scan for MCP server YAML files |

### `.max_concurrency()` Parameter

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `value` | `int` | `20` | 1-100 | Thread pool size for concurrent `invoke()` calls |

### `.set_intervals()` Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gc_interval_s` | `int \| None` | `30` | Garbage collection interval in seconds |
| `health_check_interval_s` | `int \| None` | `10` | Health check interval in seconds |

### Complete Builder Example

```python
from mcp_hangar import HangarConfig

config = (
    HangarConfig()
    .add_mcp_server(
        "math",
        mode="subprocess",
        command=["python", "-m", "math_server"],
        idle_ttl_s=600,
    )
    .add_mcp_server(
        "llm",
        mode="remote",
        url="https://llm-api.example.com/mcp",
        env={"API_KEY": "${LLM_API_KEY}"},
    )
    .add_mcp_server(
        "sandbox",
        mode="docker",
        image="mcp-sandbox:latest",
    )
    .enable_discovery(docker=True, filesystem=["/etc/mcp/mcp_servers/"])
    .max_concurrency(50)
    .set_intervals(gc_interval_s=60, health_check_interval_s=30)
    .build()
)
```

!!! warning
    Calling `.build()` freezes the configuration. Subsequent calls to `.add_mcp_server()` or other builder methods raise `ConfigurationError`. Calling `.build()` again also raises `ConfigurationError`.

Validation errors (empty MCP server name, invalid mode, missing mode-specific parameters) raise `ConfigurationError` with a descriptive message.

## API Reference

### Lifecycle

=== "Async (Hangar)"

    ```python
    # Start -- bootstraps mcp_servers and background workers
    await hangar.start()

    # Stop -- stops all mcp_servers and workers
    await hangar.stop()

    # Context manager (recommended) -- auto-calls start/stop
    async with Hangar.from_config("config.yaml") as hangar:
        ...
    ```

=== "Sync (SyncHangar)"

    ```python
    # Start
    hangar.start()

    # Stop
    hangar.stop()

    # Context manager (recommended)
    with SyncHangar.from_config("config.yaml") as hangar:
        ...
    ```

### Invocation

=== "Async (Hangar)"

    ```python
    result = await hangar.invoke(
        mcp_server_name="math",
        tool_name="add",
        arguments={"a": 1, "b": 2},
        timeout_s=30.0,  # default: 30.0
    )
    ```

=== "Sync (SyncHangar)"

    ```python
    result = hangar.invoke(
        mcp_server_name="math",
        tool_name="add",
        arguments={"a": 1, "b": 2},
        timeout_s=30.0,
    )
    ```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mcp_server_name` | `str` | required | MCP Server to invoke |
| `tool_name` | `str` | required | Tool name on the MCP server |
| `arguments` | `dict \| None` | `None` | Tool arguments |
| `timeout_s` | `float` | `30.0` | Invocation timeout in seconds |

Cold MCP servers are auto-started on first invocation.

### MCP Server Management

=== "Async (Hangar)"

    ```python
    # Start a specific mcp_server
    await hangar.start_mcp_server("math")

    # Stop a specific mcp_server
    await hangar.stop_mcp_server("math")

    # Get mcp_server state snapshot
    info: ProviderInfo = await hangar.get_mcp_server("math")

    # List all mcp_servers
    mcp_servers: list[ProviderInfo] = await hangar.list_mcp_servers()
    ```

=== "Sync (SyncHangar)"

    ```python
    hangar.start_mcp_server("math")
    hangar.stop_mcp_server("math")
    info: ProviderInfo = hangar.get_mcp_server("math")
    mcp_servers: list[ProviderInfo] = hangar.list_mcp_servers()
    ```

### Health

=== "Async (Hangar)"

    ```python
    # Health summary for all mcp_servers
    summary: HealthSummary = await hangar.health()

    # Health check for a specific mcp_server
    is_healthy: bool = await hangar.health_check("math")
    ```

=== "Sync (SyncHangar)"

    ```python
    summary: HealthSummary = hangar.health()
    is_healthy: bool = hangar.health_check("math")
    ```

## Data Classes

### ProviderInfo

Frozen dataclass representing a MCP server state snapshot.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | MCP Server name |
| `state` | `str` | Current state: `cold`, `ready`, `degraded`, `dead` |
| `mode` | `str` | MCP Server mode: `subprocess`, `docker`, `remote` |
| `tools` | `list[str]` | Available tool names |
| `last_used` | `float \| None` | Last invocation timestamp (epoch seconds) |
| `error` | `str \| None` | Error message if MCP server is in error state |

| Property | Type | Description |
|----------|------|-------------|
| `is_ready` | `bool` | `True` if `state == "ready"` |
| `is_cold` | `bool` | `True` if `state == "cold"` |

### HealthSummary

Frozen dataclass with aggregate health information.

| Field | Type | Description |
|-------|------|-------------|
| `MCP servers` | `dict[str, str]` | Mapping of MCP server name to state |
| `ready_count` | `int` | Number of MCP servers in `ready` state |
| `total_count` | `int` | Total number of MCP servers |

| Property | Type | Description |
|----------|------|-------------|
| `all_ready` | `bool` | `True` if all MCP servers are ready |
| `any_ready` | `bool` | `True` if at least one MCP server is ready |

### HangarConfigData

Dataclass holding the built configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `MCP servers` | `dict[str, dict]` | `{}` | MCP Server definitions |
| `discovery` | `DiscoverySpec` | default | Discovery configuration |
| `gc_interval_s` | `int` | `30` | Garbage collection interval |
| `health_check_interval_s` | `int` | `10` | Health check interval |
| `max_concurrency` | `int` | `20` | Thread pool size |

### DiscoverySpec

Dataclass for discovery source configuration.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `docker` | `bool` | `False` | Enable Docker discovery |
| `kubernetes` | `bool` | `False` | Enable Kubernetes discovery |
| `filesystem` | `list[str]` | `[]` | Filesystem paths to scan |

## Framework Integration

### FastAPI

Use the FastAPI lifespan event handler to manage the Hangar lifecycle. Store the instance on `app.state` for dependency injection.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from mcp_hangar import Hangar

@asynccontextmanager
async def lifespan(app: FastAPI):
    hangar = Hangar.from_config("config.yaml")
    await hangar.start()
    app.state.hangar = hangar
    yield
    await hangar.stop()

app = FastAPI(lifespan=lifespan)

@app.post("/invoke/{mcp_server}/{tool}")
async def invoke_tool(mcp_server: str, tool: str, request: Request):
    hangar: Hangar = request.app.state.hangar
    body = await request.json()
    result = await hangar.invoke(mcp_server, tool, body.get("arguments"))
    return {"result": result}

@app.get("/health")
async def health(request: Request):
    hangar: Hangar = request.app.state.hangar
    summary = await hangar.health()
    return {
        "status": "healthy" if summary.all_ready else "degraded",
        "ready": summary.ready_count,
        "total": summary.total_count,
        "mcp_servers": summary.mcp_servers,
    }
```

The `async with` context manager pattern also works in lifespan handlers:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with Hangar.from_config("config.yaml") as hangar:
        app.state.hangar = hangar
        yield
```

## Error Handling

The Facade API raises specific exceptions for different failure modes:

| Exception | When Raised |
|-----------|-------------|
| `ConfigurationError` | Invalid configuration, Hangar not started, builder already built |
| `ProviderNotFoundError` | MCP Server name does not exist in configuration |
| `ToolNotFoundError` | Tool name not found on the specified MCP server |
| `ToolInvocationError` | Tool execution failed on the MCP server side |
| `TimeoutError` | Invocation exceeded `timeout_s` |

All exceptions include descriptive messages. Catch specific exceptions for targeted error handling:

```python
from mcp_hangar.domain.exceptions import (
    ProviderNotFoundError,
    ToolInvocationError,
    ToolNotFoundError,
)

try:
    result = await hangar.invoke("math", "divide", {"a": 10, "b": 0})
except ToolInvocationError as e:
    print(f"Tool failed: {e}")
except ProviderNotFoundError as e:
    print(f"McpServer not found: {e}")
except TimeoutError:
    print("Invocation timed out")
```

For available tool names on a MCP server, see the [MCP Tools Reference](../reference/tools.md).
