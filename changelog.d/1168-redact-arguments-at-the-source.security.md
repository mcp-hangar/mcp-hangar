**core:** a tool call's arguments reached the event store and the `/ws/events`
stream verbatim, so a secret passed to a tool sat in SQLite or Postgres for the
retention of the event log and was served to every `audit:read` holder -- while
the approval record built from the same dict has been two-pass redacted since
#1130 and the log pipeline prints `[REDACTED]`. `ToolInvocationRequested` now
redacts its arguments as it is constructed, so no exit and no future
construction site can keep them, and carries `arguments_hash` over the RAW
payload so the audit trail can still tell two calls apart. The redaction moved
out of `approvals` into `domain/security/argument_redaction.py`; approvals is
unchanged in behaviour
