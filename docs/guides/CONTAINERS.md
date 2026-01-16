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
| `network` | `none`, `bridge`, `host` | `none` |
| `read_only` | Read-only root filesystem | `true` |
| `resources.memory` | Memory limit | `512m` |
| `resources.cpu` | CPU limit | `1.0` |

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
registry_invoke(provider="sqlite", tool="execute",
                arguments={"sql": "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"})

registry_invoke(provider="sqlite", tool="query",
                arguments={"sql": "SELECT * FROM users"})
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
registry_invoke(provider="memory", tool="create_entities",
                arguments={"entities": [
                    {"name": "Alice", "entityType": "Person", "observations": ["Engineer"]}
                ]})
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
registry_invoke(provider="fetch", tool="fetch",
                arguments={"url": "https://api.example.com/data"})
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
