# Quick Start

Get MCP Hangar running with Claude Desktop in under 30 seconds.

## One-Liner Install

```bash
curl -sSL https://get.mcp-hangar.io | bash && mcp-hangar init -y && mcp-hangar serve
```

That's it. Restart Claude Desktop and you have:

- **filesystem** - Read and write local files
- **fetch** - Make HTTP requests
- **memory** - Persistent key-value storage

## Prerequisites

- Python 3.11 or later
- Claude Desktop installed ([download](https://claude.ai/download))
- uvx or npx (for MCP provider packages)

## Interactive Setup

Prefer a guided experience? Use the wizard:

```bash
pip install mcp-hangar
mcp-hangar init
```

The wizard will:

1. **Detect runtimes** - finds uvx, npx, docker in your PATH
2. **Detect Claude Desktop** - finds your Claude Desktop config automatically
3. **Select providers** - choose which MCP providers to enable
4. **Test providers** - verify each provider starts correctly
5. **Update Claude Desktop** - automatically configure Claude to use MCP Hangar

**Restart Claude Desktop** after the wizard completes.

## Verify It Works

Check that your providers are configured:

```bash
mcp-hangar status
```

You should see your configured providers listed (they'll show as COLD until first use).

## Adding More Providers

Add providers anytime with:

```bash
mcp-hangar add github     # GitHub integration (needs token)
mcp-hangar add sqlite     # SQLite database access
mcp-hangar add postgres   # PostgreSQL access
```

## Available Bundles

Use bundles to quickly configure common setups:

| Bundle | Providers | Use Case |
|--------|-----------|----------|
| `starter` | filesystem, fetch, memory | General everyday use |
| `developer` | starter + github, git | Software development |
| `data` | starter + sqlite, postgres | Data analysis |

```bash
# Start fresh with a bundle
mcp-hangar init --bundle=developer
```

## Manual Configuration

If you prefer manual setup or need advanced configuration:

### 1. Create config file

Create `~/.config/mcp-hangar/config.yaml`:

```yaml
providers:
  filesystem:
    mode: subprocess
    command: [npx, -y, "@anthropic/mcp-server-filesystem"]
    args: [/Users/your-username/Documents]
    idle_ttl_s: 300

  fetch:
    mode: subprocess
    command: [npx, -y, "@anthropic/mcp-server-fetch"]
    idle_ttl_s: 300
```

### 2. Update Claude Desktop config

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "mcp-hangar": {
      "command": "mcp-hangar",
      "args": ["serve", "--config", "/Users/your-username/.config/mcp-hangar/config.yaml"]
    }
  }
}
```

### 3. Restart Claude Desktop

## Troubleshooting

### Claude Desktop not found

If the wizard can't find Claude Desktop:

```bash
mcp-hangar init --claude-config /path/to/claude_desktop_config.json
```

### Provider won't start

Check the provider status for errors:

```bash
mcp-hangar status provider-name
```

### Permission denied

Make sure you have write access to the config directories:

- MCP Hangar config: `~/.config/mcp-hangar/`
- Claude Desktop config: `~/Library/Application Support/Claude/` (macOS)

## Next Steps

- [CLI Reference](../reference/cli.md) - All CLI commands and options
- [Container Providers](../guides/CONTAINERS.md) - Using Docker/Podman providers
- [Observability](../guides/OBSERVABILITY.md) - Metrics and monitoring
- [Architecture](../architecture/OVERVIEW.md) - Understanding the design
