**cli:** a reader for the servers a user already has in an MCP client. For each
client file `init` detects -- Claude Code user (`~/.claude.json`), Claude Code
project (`./.mcp.json`), Cursor (`~/.cursor/mcp.json`) -- it parses the
top-level `mcpServers` map into typed stdio (`command`/`args`/`env`) and remote
(`url`) entries. A malformed or unknown entry is reported by name with a reason
instead of failing the read, and no `env` or `headers` value appears in what it
reports. It is not yet used by `init`: importing these servers into the Hangar
config, pinning them and rewriting the client file follow in later releases
(#1770)
