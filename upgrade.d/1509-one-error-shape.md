### every error payload a tool answers with is `error`, `error_type`, `details`

#### a failed `hangar_reload_config` answers the tool error payload

A reload that failed used to answer a layout of its own:

```json
{
  "status": "failed",
  "message": "Configuration reload failed: ...",
  "error_type": "ConfigurationError"
}
```

It now answers the payload every other tool failure uses:

```json
{
  "error": "Configuration reload failed: ...",
  "error_type": "ConfigurationError",
  "details": {}
}
```

**A client that detected a failed reload by `status == "failed"` reads `error`
(or `error_type`) instead**, and takes the message text from `error` rather than
`message`. The text itself is unchanged, and `error_type` still names the
exception class.

A successful reload is untouched: it still answers `status: "success"`,
`message`, the four `mcp_servers_*` lists and `duration_ms`.

#### `MCPError.to_dict()` is removed

`mcp_hangar.MCPError` -- and every domain exception under it -- carried a
`to_dict()` that rendered:

```json
{
  "error": "...",
  "mcp_server_id": "...",
  "operation": "...",
  "details": {},
  "type": "ConfigurationError"
}
```

Nothing in Hangar called it. The REST API builds its own envelope
(`{"error": {"code", "message", "details"}}`) from the exception's attributes,
and an MCP tool error is the three-key payload above, so this was a second
serializer that named the failure under `type` where the answering surfaces
named it `code` and `error_type`.

**An embedder that called `exc.to_dict()` builds the dict itself:**

```python
{
    "error": exc.message,
    "mcp_server_id": exc.mcp_server_id,
    "operation": exc.operation,
    "details": exc.details,
    "error_type": type(exc).__name__,
}
```

The attributes it read -- `message`, `mcp_server_id`, `operation`, `details` --
are unchanged, as is every exception class and what raises it.
