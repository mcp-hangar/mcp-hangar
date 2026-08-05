"""How an aggregate's identity becomes an event-stream identifier.

One rule, read by both sides of the store:

* `infrastructure.event_bus.publish_aggregate_events` builds the id it appends
  under,
* `application.queries.handlers` builds the id it reads back.

It lives in the shared kernel for the same reason `trusted_hosts` does: the two
sides have to agree, and nothing was making them. They did not agree. The writer
composed `f"{aggregate_type}:{aggregate_id}"` while the reader composed
`f"mcp_server-{id}"` -- a colon against a hyphen, in two files, neither of which
could observe the other. The mismatch was invisible because no writer ever ran;
it would have surfaced as an endpoint that stayed empty after the store started
filling up, which is the worst moment to discover a naming convention.

There is deliberately no parsing function, and no prefix helper either. A
stream id is written and matched, never taken apart; the first version of this
module shipped a `stream_prefix_for()` with no caller, on the theory that
listing streams would want one, and the dead-symbol gate rejected it before
review did. Speculative API is the same disease as the 22 event classes nothing
emits. Both come back when a caller does -- `MCP_SERVER_GROUP` is back here for
exactly that reason: five drain points now name it.
"""

from __future__ import annotations

from typing import Final

#: Aggregate type for `McpServer` streams.
MCP_SERVER: Final = "mcp_server"

#: Aggregate type for `McpServerGroup` streams.
MCP_SERVER_GROUP: Final = "mcp_server_group"

SEPARATOR: Final = ":"


def stream_id_for(aggregate_type: str, aggregate_id: str) -> str:
    """The stream an aggregate's events are appended to and read from.

    Args:
        aggregate_type: Aggregate type, e.g. `MCP_SERVER`.
        aggregate_id: The aggregate's own identifier.

    Returns:
        The stream identifier, e.g. `mcp_server:math`.
    """
    return f"{aggregate_type}{SEPARATOR}{aggregate_id}"
