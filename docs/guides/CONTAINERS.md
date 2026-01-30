# Container Providers

Run MCP providers in Docker or Podman containers.

## Quick Start

```bash
# Build images
podman build -t localhost/mcp-sqlite -f docker/Dockerfile.sqlite .
podman build -t localhost/mcp-memory -f docker/Dockerfile.memory .
podman build -t localhost/mcp-filesystem -f docker/Dockerfile.filesystem .
podman build -t localhost/mcp-fetch -f docker/Dockerfile.fetch .

# Create data directories
mkdir -p data/sqlite data/memory data/filesystem
```

## Configuration

```yaml
providers:
  sqlite:
    mode: container
    image: localhost/mcp-sqlite:latest
    volumes:
      - "/absolute/path/to/data:/data:rw"
    network: bridge
    idle_ttl_s: 300
    resources:
      memory: 512m
      cpu: "1.0"
```

> **Important**: Always use absolute paths. Relative paths (`./data`, `${PWD}`) fail when MCP clients start the server from different directories.

### Options

| Option | Description | Default |
|--------|-------------|---------|
| `image` | Container image | required |
| `volumes` | Mount points (`host:container:mode`) | `[]` |
| `env` | Environment variables | `{}` |
| `network` | Network mode: `none`, `bridge`, `host` | `none` |
| `network_mode` | Alias for `network` (Docker Compose compatibility) | `none` |
| `read_only` | Read-only root filesystem | `true` |
| `resources.memory` | Memory limit | `512m` |
| `resources.cpu` | CPU limit | `1.0` |

#### Network Modes

- **`none`** (default): No network access. Most secure, use for providers that don't need external connectivity.
- **`bridge`**: Isolated bridge network. Container can reach external services but is isolated from host network.
- **`host`**: Share host network namespace. Required when provider needs to connect to services on localhost or has complex networking requirements.

```yaml
# Provider that needs to connect to local Prometheus/VictoriaMetrics
prometheus:
  mode: docker
  image: ghcr.io/pab1it0/prometheus-mcp-server:latest
  network_mode: host  # or network: host
  env:
    PROMETHEUS_URL: "https://victoriametrics.example.com"
```

### Custom Build

```yaml
providers:
  custom:
    mode: container
    build:
      dockerfile: docker/Dockerfile.custom
      context: .
      tag: my-image:latest
```

## Available Images

### SQLite

```yaml
sqlite:
  mode: container
  image: localhost/mcp-sqlite:latest
  volumes:
    - "/path/to/data:/data:rw"
  network: bridge
```

Tools: `query`, `execute`, `list-tables`, `describe-table`, `create-table`

```python
hangar_call(calls=[{"provider": "sqlite", "tool": "execute",
                    "arguments": {"sql": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"}}])

hangar_call(calls=[{"provider": "sqlite", "tool": "query",
                    "arguments": {"sql": "SELECT * FROM users"}}])
```

### Memory (Knowledge Graph)

```yaml
memory:
  mode: container
  image: localhost/mcp-memory:latest
  volumes:
    - "/path/to/data:/app/data:rw"
```

Tools: `create_entities`, `create_relations`, `search_nodes`, `read_graph`

```python
hangar_call(calls=[{"provider": "memory", "tool": "create_entities",
                    "arguments": {"entities": [
                        {"name": "Alice", "entityType": "Person", "observations": ["Engineer"]}
                    ]}}])
```

### Filesystem

```yaml
filesystem:
  mode: container
  image: localhost/mcp-filesystem:latest
  volumes:
    - "/path/to/sandbox:/data:rw"
```

Tools: `read_file`, `write_file`, `list_directory`

### Fetch

```yaml
fetch:
  mode: container
  image: localhost/mcp-fetch:latest
  network: bridge
```

Tools: `fetch`

```python
hangar_call(calls=[{"provider": "fetch", "tool": "fetch",
                    "arguments": {"url": "https://api.example.com/data"}}])
```

## Troubleshooting

### Container won't start

```bash
# Verify image
podman images localhost/mcp-sqlite

# Test manually
echo '{"jsonrpc":"2.0","id":"1","method":"initialize","params":{}}' | \
  podman run --rm -i -v /path/to/data:/data:rw localhost/mcp-sqlite:latest
```

### Data not persisting

1. Use absolute paths
2. Check host directory permissions
3. Verify mount:
   ```bash
   podman run --rm -v /path/to/data:/data:rw --entrypoint sh \
     localhost/mcp-sqlite:latest -c "ls -la /data"
   ```

### Permission denied

```bash
chmod 777 data/sqlite
```

Or set `MCP_CI_RELAX_VOLUME_PERMS=true`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_CONTAINER_RUNTIME` | auto | Force `podman` or `docker` |
| `MCP_CI_RELAX_VOLUME_PERMS` | `false` | Chmod 777 on volumes (CI) |
