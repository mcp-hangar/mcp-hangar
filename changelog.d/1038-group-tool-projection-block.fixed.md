**core:** a `tool_projection:` block on a **group** is read instead of silently
dropped. Only the mcp_server branch parsed it, so a group could declare neither a
withdrawal, a digest pin nor a `digest_enforcement` mode -- the key loaded without
a warning and did nothing, which left the group with no id under which those
controls could be both declared and read
