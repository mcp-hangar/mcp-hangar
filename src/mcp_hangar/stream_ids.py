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

#: Aggregate type for session streams. A session is not an aggregate the way a
#: server is -- nothing owns its lifecycle -- but decisions *about* a session
#: have to reach every replica, and the log is how anything reaches every
#: replica. Suspending one is the case that matters: it is a security decision
#: about the session, not about the pod that happened to take the request.
SESSION: Final = "session"

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


def stream_id_for_event(event: object) -> str | None:
    """Which stream this event belongs to, read off the event itself.

    An event that names an aggregate is that aggregate's history. Deriving the
    stream here rather than at the call site is what lets `EventBus.publish`
    persist by construction: the alternative was two publish methods, one of
    which silently kept no record, and 34 call sites picked the forgetful one
    against 10 that did not (#772).

    Args:
        event: A domain event.

    Returns:
        The stream id, or None when the event names no aggregate -- a config
        reload, a batch outcome. Those are delivered and not stored; there is no
        history for them to be part of, and inventing a bucket to hold them
        would make the store harder to read, not more complete.
    """
    mcp_server_id = getattr(event, "mcp_server_id", None)
    if isinstance(mcp_server_id, str) and mcp_server_id:
        return stream_id_for(MCP_SERVER, mcp_server_id)

    # Discovery names a server before it has an id, and the name it reports is
    # the id the server is registered under -- so the two are the same subject
    # at two moments, and belong in one stream. That is what makes the history
    # readable end to end: discovered, registered, started, and for one that
    # never got in, discovered and then quarantined.
    mcp_server_name = getattr(event, "mcp_server_name", None)
    if isinstance(mcp_server_name, str) and mcp_server_name:
        return stream_id_for(MCP_SERVER, mcp_server_name)

    group_id = getattr(event, "group_id", None)
    if isinstance(group_id, str) and group_id:
        return stream_id_for(MCP_SERVER_GROUP, group_id)

    # Last, so it only catches events that name a session and nothing else.
    # `DetectionRuleMatched` and `EnforcementActionTaken` both carry a session
    # *and* a server, and they belong in the server's history -- the session is
    # context there, not the subject.
    session_id = getattr(event, "session_id", None)
    if isinstance(session_id, str) and session_id:
        return stream_id_for(SESSION, session_id)

    return None
