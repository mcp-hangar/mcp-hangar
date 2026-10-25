**cli:** a backup of a private file is private too. `mcp-hangar pin --write` kept
the previous config as `<config>.bak`, and `mcp-hangar init` copied the MCP
client's config into its state directory, both at the umask default (0644)
whatever the original's mode -- and a client config often holds other servers'
tokens. Both are now copies that keep the original's mode, and the init backup
directory is created owner-only
