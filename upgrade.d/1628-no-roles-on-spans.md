### `set_governance_attributes` is removed

The 2.22.0 upgrade note said `set_governance_attributes` in
`mcp_hangar.observability.conventions` was kept for an ADR-007 adapter. It is
now removed: it wrote `mcp.caller.roles` onto a span, which the telemetry data
contract never allows, and the caller's own ids without the
`observability.tracing.caller_ids` opt-in. Nothing in Hangar called it.

An adapter that called it sets the attributes it needs itself, with the
constants that stay in `mcp_hangar.observability.conventions`:

```python
# before
from mcp_hangar.observability.conventions import set_governance_attributes
set_governance_attributes(span, mcp_server_id="math", tool_name="add", caller_type="human")

# after
from mcp_hangar.observability.conventions import Caller, GenAI, McpServer
span.set_attribute(McpServer.ID, "math")
span.set_attribute(GenAI.TOOL_NAME, "add")
span.set_attribute(Caller.TYPE, "human")
```

Leave caller roles off: they belong on the audit record. Caller ids go on a span
only when the operator turned `observability.tracing.caller_ids` on.
