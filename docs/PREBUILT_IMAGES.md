# Using Pre-built Docker Images

> **When to use this guide:** Use this guide when you want to run an existing MCP server image from a registry (Docker Hub, GitHub Container Registry, etc.) without writing a Dockerfile. For building custom images or general container configuration, see [DOCKER_SUPPORT.md](DOCKER_SUPPORT.md).

MCP Hangar allows you to use **pre-built Docker/Podman images** without creating a Dockerfile. You can use images from:

- Docker Hub
- GitHub Container Registry (ghcr.io)
- Private registries
- Local images

## Quick Start

### 1. Configure the provider

```yaml
providers:
  prometheus:
    mode: container
    image: ghcr.io/pab1it0/prometheus-mcp-server:latest
    env:
      PROMETHEUS_URL: "http://localhost:9090"
    network: bridge
    idle_ttl_s: 600
```

No `build` section needed.

### 2. Run the registry

```bash
export MCP_CONFIG=config.prebuilt.yaml
python -m mcp_hangar.server
```

### 3. Invoke the provider

```json
{
  "provider": "prometheus",
  "tool": "query_prometheus",
  "arguments": {"query": "up"}
}
```

## Configuration

### Minimal

```yaml
providers:
  my_provider:
    mode: container
    image: ghcr.io/org/mcp-server:latest
```

### Full

```yaml
providers:
  my_provider:
    mode: container
    image: ghcr.io/org/mcp-server:latest

    env:
      API_KEY: "${API_KEY}"
      DATABASE_URL: "postgresql://localhost/db"

    volumes:
      - "./data:/app/data:rw"
      - "${HOME}/.config:/config:ro"

    resources:
      memory: 512m
      cpu: "1.0"

    network: bridge  # none, bridge, host
    read_only: false
    user: "current"

    idle_ttl_s: 600
    health_check_interval_s: 60
    max_consecutive_failures: 3
```

## Migration from Claude Desktop

**Claude Desktop (`claude_desktop_config.json`):**
```json
{
  "mcpServers": {
    "prometheus": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-e", "PROMETHEUS_URL", "ghcr.io/pab1it0/prometheus-mcp-server:latest"],
      "env": {"PROMETHEUS_URL": "http://localhost:9090"}
    }
  }
}
```

**MCP Hangar (`config.yaml`):**
```yaml
providers:
  prometheus:
    mode: container
    image: ghcr.io/pab1it0/prometheus-mcp-server:latest
    env:
      PROMETHEUS_URL: "http://localhost:9090"
    network: bridge
```

## Image Sources

### Docker Hub

```yaml
providers:
  my_provider:
    mode: container
    image: username/mcp-server:v1.0.0
```

### GitHub Container Registry

```yaml
providers:
  my_provider:
    mode: container
    image: ghcr.io/organization/mcp-server:latest
```

### Private Registry

```bash
podman login registry.example.com
```

```yaml
providers:
  my_provider:
    mode: container
    image: registry.example.com/org/mcp-server:v2.1.0
```

### Local Image

```bash
podman build -t my-mcp:latest .
```

```yaml
providers:
  my_provider:
    mode: container
    image: my-mcp:latest
```

## Examples

### Prometheus Monitoring

```yaml
providers:
  prometheus:
    mode: container
    image: ghcr.io/pab1it0/prometheus-mcp-server:latest
    env:
      PROMETHEUS_URL: "http://prometheus.example.com:9090"
    network: bridge
    resources:
      memory: 256m
      cpu: "0.5"
    idle_ttl_s: 600
```

### Database Access

```yaml
providers:
  postgres:
    mode: container
    image: ghcr.io/example/postgres-mcp:latest
    env:
      DATABASE_URL: "${DATABASE_URL}"
      DATABASE_READONLY: "true"
    network: bridge
    resources:
      memory: 512m
      cpu: "1.0"
    idle_ttl_s: 300
```

### File Processing with Volumes

```yaml
providers:
  file_processor:
    mode: container
    image: myorg/file-processor-mcp:v1.0
    volumes:
      - "./input:/data/input:ro"
      - "./output:/data/output:rw"
    resources:
      memory: 1024m
      cpu: "2.0"
    read_only: false
    network: none
```

## Security

MCP Hangar automatically applies security settings:

```yaml
providers:
  my_provider:
    mode: container
    image: example/mcp:latest
    # Applied automatically:
    # --cap-drop ALL
    # --security-opt no-new-privileges
    # --read-only (if read_only: true)
    # --network none (if network: none)
```

### Disabling Read-Only

If the provider needs to write to the filesystem:

```yaml
providers:
  my_provider:
    mode: container
    image: example/mcp:latest
    read_only: false
    volumes:
      - "./data:/app/data:rw"
```

### User Mapping

If the provider needs access to user files:

```yaml
providers:
  my_provider:
    mode: container
    image: example/mcp:latest
    user: "current"
    volumes:
      - "${HOME}:/data:ro"
```

## Troubleshooting

### Provider doesn't start

```bash
podman images | grep mcp-server
podman pull ghcr.io/org/mcp-server:latest
podman run -i --rm ghcr.io/org/mcp-server:latest
```

### Permission error

```yaml
providers:
  my_provider:
    user: "current"
```

### Needs network access

```yaml
providers:
  my_provider:
    network: bridge
```

### "Permission denied" when writing

```yaml
providers:
  my_provider:
    read_only: false
    volumes:
      - "./data:/app/data:rw"
```

## Build vs Image Comparison

| Aspect | Build (Dockerfile) | Pre-built Image |
|--------|-------------------|-----------------|
| Startup | Slower (build step) | Fast |
| Control | Full | Limited |
| Updates | Manual rebuild | Pull latest |
| Registry | Not required | Required |

## CI/CD Integration

### GitHub Actions

```yaml
- name: Test MCP Hangar with Pre-built Images
  run: |
    podman pull ghcr.io/org/mcp-server:latest
    export MCP_CONFIG=config.prebuilt.yaml
    pytest tests/feature/test_prebuilt_image.py -v
```

### Docker Compose

```yaml
version: '3.8'

services:
  mcp-hangar:
    build: .
    environment:
      - MCP_CONFIG=config.prebuilt.yaml
    volumes:
      - ./config.prebuilt.yaml:/app/config.prebuilt.yaml
      - /var/run/podman/podman.sock:/var/run/docker.sock
```

See `config.prebuilt.yaml` for more examples.
