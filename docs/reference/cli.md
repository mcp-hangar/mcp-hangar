# CLI Reference

MCP Hangar provides a comprehensive command-line interface for managing MCP servers.

## Installation

```bash
pip install mcp-hangar
# or
uv pip install mcp-hangar
```

## Synopsis

```bash
mcp-hangar [OPTIONS] COMMAND [ARGS]...
```

## Global Options

These options are available for all commands:

| Option | Short | Type | Default | Env Variable | Description |
|--------|-------|------|---------|--------------|-------------|
| `--config` | `-c` | PATH | - | `MCP_CONFIG` | Path to config.yaml file |
| `--verbose` | `-v` | FLAG | false | - | Show verbose output including debug information |
| `--quiet` | `-q` | FLAG | false | - | Suppress non-essential output |
| `--json` | - | FLAG | false | - | Output in JSON format for scripting |
| `--version` | `-V` | FLAG | - | - | Show version and exit |
| `--help` | - | FLAG | - | - | Show help message and exit |

## Commands

| Command | Description |
|---------|-------------|
| [`init`](#init) | Interactive setup wizard |
| [`status`](#status) | Show MCP server health dashboard |
| [`add`](#add) | Add MCP server from registry |
| [`remove`](#remove) | Remove MCP server from configuration |
| [`serve`](#serve) | Start the MCP server |
| [`completion`](#completion) | Generate shell completion scripts |

---

## init

Interactive setup wizard for MCP Hangar. Guides you through MCP server selection and configuration in under 5 minutes.

### Synopsis

```bash
mcp-hangar init [OPTIONS]
```

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--non-interactive` | `-y` | FLAG | false | Run without prompts, using defaults |
| `--bundle` | `-b` | TEXT | - | MCP Server bundle to install |
| `--MCP servers` | - | TEXT | - | Comma-separated list of MCP servers |
| `--config-path` | - | PATH | - | Custom path for config file |
| `--claude-config` | - | PATH | - | Custom path to Claude Desktop config |
| `--skip-claude` | - | FLAG | false | Skip Claude Desktop config modification |
| `--reset` | - | FLAG | false | Reset existing configuration |

### MCP Server Bundles

| Bundle | MCP servers | Use Case |
|--------|-----------|----------|
| `starter` | filesystem, fetch, memory | General use, getting started |
| `developer` | filesystem, fetch, memory, github, git | Software development |
| `data` | filesystem, fetch, memory, sqlite, postgres | Data analysis |

### Examples

```bash
# Interactive setup
mcp-hangar init

# Install starter bundle
mcp-hangar init --bundle starter

# Install specific mcp_servers
mcp-hangar init --mcp-servers filesystem,github,sqlite

# Non-interactive with developer bundle
mcp-hangar init -y --bundle developer

# Custom config location
mcp-hangar init --config-path ~/my-config.yaml

# Skip Claude Desktop integration
mcp-hangar init --skip-claude
```

### What It Does

1. Detects Claude Desktop installation
2. Presents MCP server categories for selection
3. Collects required configuration (API keys, paths)
4. Generates `config.yaml` file
5. Updates Claude Desktop configuration
6. Shows next steps

---

## status

Display health dashboard of all configured MCP servers with real-time updates.

### Synopsis

```bash
mcp-hangar status [OPTIONS] [PROVIDER]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `PROVIDER` | No | Show detailed status for specific MCP server |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--watch` | `-w` | FLAG | false | Continuously update the display |
| `--interval` | `-i` | FLOAT | 2.0 | Update interval in seconds (with --watch) |
| `--details` | `-d` | FLAG | false | Show additional columns (mode, memory, uptime) |

### MCP Server States

| State | Indicator | Description |
|-------|-----------|-------------|
| READY | `OK` (green) | MCP Server is running and healthy |
| COLD | `--` (dim) | MCP Server not started |
| INITIALIZING | `..` (cyan) | MCP Server starting up |
| DEGRADED | `!!` (yellow) | MCP Server has issues |
| DEAD | `XX` (red) | MCP Server failed/crashed |

### Examples

```bash
# Show all mcp_servers
mcp-hangar status

# Watch mode with live updates
mcp-hangar status --watch

# Faster refresh rate
mcp-hangar status -w -i 0.5

# Show detailed information
mcp-hangar status --details

# Single mcp_server details
mcp-hangar status github

# JSON output for scripting
mcp-hangar --json status
```

### Output Columns

**Standard view:**

- MCP Server name
- State indicator
- Tools count

**Detailed view (`--details`):**

- MCP Server name
- State indicator
- Mode (subprocess/docker/remote)
- Tools count
- Memory usage
- Uptime

---

## add

Add a MCP server from the MCP Registry to your configuration.

### Synopsis

```bash
mcp-hangar add [OPTIONS] NAME
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | Yes | MCP Server name or search query |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--search` | `-s` | FLAG | false | Search registry instead of exact match |
| `--yes` | `-y` | FLAG | false | Skip confirmation prompts |
| `--no-reload` | - | FLAG | false | Don't hot-reload running server |

### Available MCP servers

| MCP Server | Description | Requires Config |
|----------|-------------|-----------------|
| `filesystem` | File system access | Yes (allowed paths) |
| `fetch` | HTTP requests | No |
| `memory` | Key-value storage | No |
| `github` | GitHub API | Yes (token) |
| `git` | Git operations | No |
| `sqlite` | SQLite databases | Yes (database path) |
| `postgres` | PostgreSQL databases | Yes (connection string) |
| `brave-search` | Brave Search API | Yes (API key) |
| `puppeteer` | Browser automation | No |
| `slack` | Slack integration | Yes (token) |
| `google-drive` | Google Drive access | Yes (credentials) |
| `google-maps` | Google Maps API | Yes (API key) |
| `sentry` | Sentry error tracking | Yes (token) |
| `raygun` | Raygun monitoring | Yes (API key) |
| `everart` | Everart API | Yes (API key) |
| `sequential-thinking` | Reasoning chains | No |

### Examples

```bash
# Add by exact name
mcp-hangar add github

# Search for mcp_servers
mcp-hangar add --search database

# Skip confirmation
mcp-hangar add filesystem -y

# Add without hot-reload
mcp-hangar add postgres --no-reload
```

### Configuration Prompts

When adding a MCP server that requires configuration, you'll be prompted for:

- **Secrets** (API keys, tokens): Hidden input, stored securely
- **Paths** (directories, files): Path validation
- **Text** (URLs, names): Standard input

Environment variables are detected automatically. If `GITHUB_TOKEN` is set, you'll be asked whether to use it.

---

## remove

Remove a MCP server from your configuration.

### Synopsis

```bash
mcp-hangar remove [OPTIONS] NAME
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `NAME` | Yes | MCP Server name to remove |

### Options

| Option | Short | Type | Default | Description |
|--------|-------|------|---------|-------------|
| `--yes` | `-y` | FLAG | false | Skip confirmation prompt |
| `--keep-running` | - | FLAG | false | Don't stop running MCP server instance |

### Examples

```bash
# Remove with confirmation
mcp-hangar remove github

# Remove without confirmation
mcp-hangar remove filesystem -y

# Remove from config but keep running
mcp-hangar remove postgres --keep-running
```

### Behavior

1. Validates MCP server exists in configuration
2. Prompts for confirmation (unless `-y`)
3. Stops running instance (unless `--keep-running`)
4. Removes from config.yaml
5. Attempts hot-reload of server

---

## serve

Start the MCP Hangar server. This is the default command when no subcommand is specified.

### Synopsis

```bash
mcp-hangar serve [OPTIONS]
# or simply:
mcp-hangar [OPTIONS]
```

### Options

| Option | Short | Type | Default | Env Variable | Description |
|--------|-------|------|---------|--------------|-------------|
| `--http` | - | FLAG | false | `MCP_MODE=http` | Run in HTTP mode |
| `--host` | - | TEXT | 0.0.0.0 | `MCP_HTTP_HOST` | HTTP server host |
| `--port` | `-p` | INT | 8000 | `MCP_HTTP_PORT` | HTTP server port |
| `--log-file` | - | PATH | - | - | Path to log file |
| `--log-level` | - | TEXT | INFO | `MCP_LOG_LEVEL` | Log level |
| `--json-logs` | - | FLAG | false | `MCP_JSON_LOGS` | Format logs as JSON |

### Transport Modes

**stdio (default)**

JSON-RPC over stdin/stdout. Used by Claude Desktop and similar clients.

```bash
mcp-hangar serve
mcp-hangar --config config.yaml serve
```

**HTTP**

HTTP server with Streamable HTTP transport. Used by LM Studio and web clients.

```bash
mcp-hangar serve --http
mcp-hangar serve --http --port 9000
mcp-hangar serve --http --host 127.0.0.1 --port 8080
```

### HTTP Endpoints

When running in HTTP mode:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/mcp` | POST/GET | MCP protocol endpoint |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |
| `/health/startup` | GET | Startup probe |
| `/metrics` | GET | Prometheus metrics |

### Log Levels

- `DEBUG` - Detailed debugging information
- `INFO` - General operational information (default)
- `WARNING` - Warning messages
- `ERROR` - Error messages only
- `CRITICAL` - Critical errors only

### Examples

```bash
# stdio mode (for Claude Desktop)
mcp-hangar serve

# HTTP mode on default port
mcp-hangar serve --http

# HTTP mode with custom port
mcp-hangar serve --http -p 9000

# With debug logging
mcp-hangar serve --log-level DEBUG

# With log file
mcp-hangar serve --log-file /var/log/mcp-hangar.log

# JSON logs for log aggregation
mcp-hangar serve --json-logs

# Full production setup
mcp-hangar serve --http --host 0.0.0.0 --port 8000 \
  --log-level INFO --json-logs --log-file /var/log/mcp.log
```

---

## completion

Generate shell completion scripts for tab-completion support.

### Synopsis

```bash
mcp-hangar completion COMMAND
```

### Subcommands

| Command | Description |
|---------|-------------|
| `bash` | Generate bash completion script |
| `zsh` | Generate zsh completion script |
| `fish` | Generate fish completion script |
| `install` | Auto-install completion for detected shell |

### Installation

**Bash**

```bash
# System-wide
mcp-hangar completion bash | sudo tee /etc/bash_completion.d/mcp-hangar

# User-only
mcp-hangar completion bash >> ~/.bashrc
```

**Zsh**

```bash
# Add to fpath
mcp-hangar completion zsh > ~/.zfunc/_mcp-hangar

# Add to .zshrc if not already present:
# fpath=(~/.zfunc $fpath)
# autoload -Uz compinit && compinit
```

**Fish**

```bash
mcp-hangar completion fish > ~/.config/fish/completions/mcp-hangar.fish
```

**Auto-install**

```bash
# Detect shell and install
mcp-hangar completion install

# Specify shell
mcp-hangar completion install zsh
```

---

## Configuration File

### Default Locations

The CLI searches for configuration in this order:

1. `--config` option
2. `MCP_CONFIG` environment variable
3. `~/.config/mcp-hangar/config.yaml`
4. `./config.yaml` (current directory)

### Example Configuration

```yaml
mcp_servers:
  filesystem:
    mode: subprocess
    command:
      - npx
      - -y
      - "@modelcontextprotocol/server-filesystem"
      - "/home/user/documents"

  github:
    mode: subprocess
    command:
      - npx
      - -y
      - "@modelcontextprotocol/server-github"
    env:
      GITHUB_TOKEN: ${GITHUB_TOKEN}

  my-api:
    mode: remote
    endpoint: https://api.example.com/mcp

logging:
  level: INFO
  json_format: false

event_store:
  enabled: true
  driver: sqlite
  path: data/events.db
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MCP_CONFIG` | Path to configuration file | - |
| `MCP_MODE` | Server mode (`stdio` or `http`) | `stdio` |
| `MCP_HTTP_HOST` | HTTP server host | `0.0.0.0` |
| `MCP_HTTP_PORT` | HTTP server port | `8000` |
| `MCP_LOG_LEVEL` | Log level | `INFO` |
| `MCP_JSON_LOGS` | Enable JSON logging | `false` |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | User error (invalid input, missing file, permission denied) |
| 2 | System error (network failure, MCP server crash) |
| 130 | Interrupted by user (Ctrl+C) |

---

## See Also

- [Quick Start Guide](../getting-started/quickstart.md)
- [HTTP Transport Guide](../guides/HTTP_TRANSPORT.md)
