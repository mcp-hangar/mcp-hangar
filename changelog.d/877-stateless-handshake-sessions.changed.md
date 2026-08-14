`serve --http` now serves the handshake-era MCP transport statelessly, so replicas
of one gateway are one server to a client. A session lived in a single replica's
memory, so a client that initialized against one pod and called against another was
told `Session not found`; session affinity could not fix that, because a pin does not
outlive its pod. `initialize` no longer returns an `Mcp-Session-Id`, a stale one is
ignored rather than refused, and `DELETE /mcp` answers 405 because there is no session
to terminate — see UPGRADE.md. The 2026-07-28 revision is unaffected: SEP-2567 removed
sessions and it was already served this way. Session suspension, authorization and
resumability are unchanged. (#877)
