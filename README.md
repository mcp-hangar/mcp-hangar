# MCP Hangar

Parallel MCP tool execution. One interface. 50x faster.

## The Problem

Your AI agent calls 5 tools sequentially. Each takes 200ms. That's 1 second of waiting.

Your agent could call them in parallel. 200ms total. But MCP doesn't do that.

## The Solution

```bash
pip install mcp-hangar
```

One tool: `hangar_call`. Pass it a list of calls. Get parallel execution. Done.

## Quick Start

**1. Create config** (`~/.hangar/config.yaml`):

```yaml
providers:
  - id: github
    command: ["uvx", "mcp-server-github"]
  - id: slack
    command: ["uvx", "mcp-server-slack"]
```

**2. Add to Claude Code** (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "hangar": {
      "command": "mcp-hangar",
      "args": ["serve"]
    }
  }
}
```

**3. Restart Claude Code.**

Now `hangar_call([...])` executes all your tools in parallel.

## Benchmarks

| Scenario | Sequential | Hangar | Speedup |
|----------|-----------|--------|---------|
| 15 tools, 2 providers | ~20s | 380ms | 50x |
| 10 tools, thundering herd | ~3s | 320ms | 10x |

100% success rate. <10ms overhead.

## That's It

No features to configure. No modes to choose. No architecture to understand.

One tool. Parallel execution. Ship it.

---

[Docs](https://mcp-hangar.io) · [PyPI](https://pypi.org/project/mcp-hangar/) · [GitHub](https://github.com/mapyr/mcp-hangar)
