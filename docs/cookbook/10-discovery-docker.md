# 10 -- Discovery: Docker

> **Prerequisite:** [01 -- HTTP Gateway](01-http-gateway.md)
> **You will need:** Running Hangar, Docker or Podman
> **Time:** 10 minutes
> **Adds:** Auto-discover MCP servers from Docker container labels

## The Problem

You have MCP servers running as Docker containers. You don't want to manually update `config.yaml` every time a container starts or stops. You want Hangar to detect them automatically.

## The Config

```yaml
# config.yaml -- Recipe 10: Docker Discovery
discovery:                               # NEW: discovery configuration
  enabled: true                          # NEW: enable auto-discovery
  refresh_interval_s: 30                 # NEW: scan every 30 seconds
  auto_register: false                   # NEW: require manual approval

  sources:                               # NEW: discovery sources
    - type: docker                       # NEW: Docker source
      mode: additive                     # NEW: only add, never remove
```

## Try It

1. Start an MCP server container with labels:

   ```bash
   docker run -d --name my-mcp-server \
     -l mcp.hangar.enabled=true \
     -l mcp.hangar.name=docker-math \
     -l mcp.hangar.mode=http \
     -l mcp.hangar.port=8080 \
     my-mcp-server:latest
   ```

2. Start Hangar:

   ```bash
   mcp-hangar serve --http --port 8000
   ```

3. Trigger a discovery scan:

   ```bash
   curl -X POST http://localhost:8000/api/discovery/sources
   ```

4. Check pending MCP servers:

   ```bash
   curl http://localhost:8000/api/discovery/pending
   ```

   ```json
   {"pending": [{"name": "docker-math", "source": "docker", "mode": "remote"}]}
   ```

5. Approve the MCP server:

   ```bash
   curl -X POST http://localhost:8000/api/discovery/approve/docker-math
   ```

6. Verify it's registered:

   ```bash
   mcp-hangar status
   ```

   ```
   docker-math    remote    cold    source=docker:auto-discovery
   ```

## What Just Happened

The Docker discovery source connects to the Docker socket and lists containers with `mcp.hangar.enabled=true` labels. In `additive` mode, it only adds new MCP servers -- never removes existing ones. With `auto_register: false`, discovered MCP servers go to a pending queue for manual approval.

Set `auto_register: true` if you trust all labeled containers and want zero-touch registration.

## Key Config Reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `discovery.enabled` | bool | `false` | Enable auto-discovery |
| `discovery.refresh_interval_s` | int | `30` | Seconds between scans |
| `discovery.auto_register` | bool | `false` | Register without approval |
| `discovery.sources[].type` | string | -- | `docker`, `filesystem`, `kubernetes`, `entrypoint` |
| `discovery.sources[].mode` | string | -- | `additive` (add only) or `authoritative` (add and remove) |

### Docker Labels

| Label | Required | Default | Description |
|-------|----------|---------|-------------|
| `mcp.hangar.enabled` | Yes | -- | Must be `"true"` |
| `mcp.hangar.name` | No | Container name | MCP Server name |
| `mcp.hangar.mode` | No | `http` | MCP Server mode |
| `mcp.hangar.port` | No | `8080` | MCP Server port |
| `mcp.hangar.group` | No | -- | Auto-add to group |

## What's Next

Docker discovery works for local and CI environments. For Kubernetes, you need annotation-based discovery.

--> [11 -- Discovery: Kubernetes](11-discovery-kubernetes.md)
